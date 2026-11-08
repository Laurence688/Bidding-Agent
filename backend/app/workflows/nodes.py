from app.telemetry.tracing import child_span
import json
import re
import time
from uuid import uuid5, NAMESPACE_URL
from app.domain.errors import DomainError
from app.domain.identifiers import canonical, content_hash, utc_now
from app.domain.decisions import aggregate_claims, NC_RESPONSE
from app.domain.output_checks import validate_coverage, validate_response, response_issues
from app.domain.split_validation import resolve_claims
from app.model_gateway.contracts import ModelRequest
from app.schemas.core import Claim, ClaimAssessment, CitationCheck, ClauseResult, EvidenceRef, RunContext
from app.schemas.model_outputs import TranslationOutput, SplitOutput, JudgeOutput, SemanticOutput, RenderOutput
from app.workflows.prompts import render_prompt, ALLOWED
from app.domain.source_context import model_source_context, context_query
from app.workflows.routing import product_conflicts, untrusted_instruction
from app.rag.admission import admit_evidence
from app.services.result_sources import source_label
from app.domain.parameters import is_parameter_requirement, validate_parameter_values, needs_parameter_value, parameter_facts, missing_parameter_threshold
from app.domain.response_contract import source_suffix, response_body
from app.model_gateway.diagnostics import failure_category
from app.domain.requirement_relations import project_fact_reason
from app.domain.retrieval_budget import supplement_reason, bound_candidates

SUPPLEMENT_REASONS = {'EVIDENCE_MISSING', 'INSUFFICIENT_EVIDENCE', 'EVIDENCE_INSUFFICIENT', 'STANDARD_DRAFT_ONLY'}


def _claim_content(claim):
    return {k: v for k, v in claim.items() if k not in {'id', 'clause_id', 'source_spans'}}


class SocNodes:
    def __init__(self, repo, settings, gateway, retriever, knowledge, prompt_bundle=None, *, original_input=False):
        self.repo, self.settings, self.gateway = repo, settings, gateway
        self.retriever, self.knowledge, self.prompt_bundle = retriever, knowledge, prompt_bundle
        self.original_input = original_input

    def call(self, state, node, data, output_model, claim_id='__clause__'):
        ctx = RunContext.model_validate(state['context'])
        data = dict(data)
        if self.original_input and node in {'judge_claim', 'verify_semantic_support'}:
            claim = data['claim']
            data['claim'] = _claim_content(claim)
            if 'claims' in claim:
                data['claim']['claims'] = [_claim_content(item) for item in claim['claims']]
            data['evidence'] = [{k: v for k, v in ref.items() if k != 'id'} for ref in data['evidence']]
        original_context = model_source_context(ctx.source_context)
        if original_context and 'source_context' in ALLOWED[node]:
            data['source_context'] = original_context
        if ctx.lifecycle_context.reference_date and 'lifecycle_context' in ALLOWED[node]:
            data['lifecycle_context'] = ctx.lifecycle_context.model_dump(mode='json')
        messages, fingerprint = render_prompt(node, data, output_model.model_json_schema(), self.prompt_bundle)
        run = {} if ctx.mode == 'EVALUATION' else self.repo.get('run', ctx.run_id)
        if run.get('context_manifest'):
            from app.services.context_management import ContextService
            config=self.repo.get('config_snapshot',ctx.config_snapshot_id)
            messages=ContextService(self.repo,self.settings).prepare_messages(run,config,self.gateway,node,output_model,data,
                lambda values,history:render_prompt(node,{**values,'history':history},output_model.model_json_schema(),self.prompt_bundle)[0],state['clause_id'],claim_id)
        kind = 'supplement' if state.get('supplement_count') and node in {'judge_claim', 'verify_semantic_support'} and claim_id != '__output__' else 'initial'
        result = self.gateway.invoke(ModelRequest(ctx.run_id, state['clause_id'], claim_id, node, messages, output_model,
                                                 ctx.config_snapshot_id, call_kind=kind, retry_invalid_output=self.original_input))
        if result.error_code:
            raise DomainError(result.error_code, '模型节点未完成，请查看执行原因', 503)
        return result.payload

    def run_node(self, node, state):
        key = state['context']['run_id'] + ':' + state['clause_id'] + ':' + node + ':' + content_hash({k: v for k, v in state.items() if k != 'trace'})
        with child_span(key, node, {'claims': state.get('claims', []), 'candidates_by_claim': state.get('candidates_by_claim', {}), 'supplement_count': state.get('supplement_count', 0)}) as span:
            from app.services.activity import ActivityService
            activity=ActivityService(self.repo)
            ctx=state['context']
            if state.get('fatal') and node not in {'aggregate','validate_output','commit_result'}:
                return {}
            with activity.step(ctx['run_id'],ctx.get('job_id'),node,state['clause_id']) as step_id:
                self._activity_step_id=step_id
                output = self._run_node(node, state)
                if output.get('fatal') and step_id:
                    self.repo.update('activity_step',step_id,{'status':'failed'})
            span.finish(output)
            return output

    def _run_node(self, node, state):
        if state.get('fatal') and node not in {'aggregate', 'validate_output', 'commit_result'}:
            return {}
        ctx = state['context']
        if ctx.get('job_id'):
            job = self.repo.get('job', ctx['job_id'])
            if job.get('cancel_requested'):
                raise DomainError('JOB_CANCELLED', '已取消后续步骤', 409)
        input_hash = content_hash({k: v for k, v in state.items() if k != 'trace'})
        key = (ctx['run_id'], state['clause_id'], node, input_hash)
        with self.repo.connection() as db:
            cached = db.execute('SELECT output FROM node_commits WHERE run_id=? AND clause_id=? AND node_name=? AND input_hash=?', key).fetchone()
        if cached:
            from app.services.activity import ActivityService
            ActivityService(self.repo).reused(getattr(self,'_activity_step_id',None))
            from app.telemetry.tracing import ACTIVE
            active = ACTIVE.get()
            if active:
                active.payload['extra']['metadata']['cache_reuse'] = True
            return json.loads(cached['output'])
        started = time.monotonic()
        if node == 'commit_result':
            # 业务结果、节点提交与事件同一事务；LangGraph 随后写入独立检查点库。
            with self.repo.transaction():
                output = self._execute(node, state)
                self._save_node(key, output, ctx, time.monotonic() - started)
            return output
        output = self._execute(node, state)
        self._save_node(key, output, ctx, time.monotonic() - started)
        return output

    def _execute(self, node, state):
        try:
            output = getattr(self, node)(state)
        except DomainError as exc:
            if exc.code in {'JOB_CANCELLED','EXECUTION_OFFLINE'}:
                raise
            output = {'fatal': exc.code, 'decision': 'UNKNOWN', 'response_en': None, 'output_valid': False,
                      'reason_codes': list(dict.fromkeys([*state.get('reason_codes', []), exc.code])),
                      'diagnostics': {**state.get('diagnostics', {}), 'failure': {'node': node, 'code': exc.code, 'category': failure_category(exc.code)}}}
        return {**output, 'trace': [*state.get('trace', []), node]}

    def _save_node(self, key, output, context, elapsed):
        with self.repo.transaction() as db:
            db.execute('INSERT OR IGNORE INTO node_commits VALUES(?,?,?,?,?,?)', (*key, canonical(output), utc_now()))
            db.execute('INSERT OR IGNORE INTO node_timings VALUES(?,?,?,?,?,?)', (*key, elapsed, utc_now()))
            if context.get('job_id'):
                self.repo.event(context['job_id'], 'clause.committed' if key[2] == 'commit_result' else 'job.progress',
                    {'clause_id': key[1], 'current_node': key[2], 'elapsed_seconds': elapsed})
                self.repo.update('job', context['job_id'], {'current_node': key[2]})

    def validate_input(self, state):
        raw, ctx = state['raw_text'], state['context']
        if not raw.strip() or len(raw) > 12000:
            return {'fatal': 'CLAIM_INCOMPLETE', 'reason_codes': ['CLAIM_INCOMPLETE']}
        if product_conflicts(raw, ctx['product_id'], self.settings.products):
            return {'fatal': 'PRODUCT_CONFLICT', 'reason_codes': ['PRODUCT_CONFLICT']}
        if untrusted_instruction(raw):
            return {'fatal': 'UNTRUSTED_INPUT', 'reason_codes': ['UNTRUSTED_INPUT']}
        source_text = context_query(ctx.get('source_context', {}))
        if len(source_text) > 4000:
            return {'fatal': 'CONTEXT_LIMIT', 'reason_codes': ['CONTEXT_LIMIT']}
        if untrusted_instruction(source_text):
            return {'fatal': 'UNTRUSTED_INPUT', 'reason_codes': ['UNTRUSTED_INPUT']}
        return {'diagnostics': {**state.get('diagnostics', {}),
                'requirement_values': model_source_context(ctx.get('source_context', {})).get('requirement_values', [])}}

    def translate(self, state):
        result = self.call(state, 'translate', {'raw_text': state['raw_text']}, TranslationOutput)
        if result['ambiguous']:
            return {'translation_zh': result['translation_zh'], 'fatal': 'TRANSLATION_AMBIGUOUS', 'reason_codes': ['CLAIM_INCOMPLETE', 'TRANSLATION_AMBIGUOUS']}
        return {'translation_zh': result['translation_zh']}

    def split_claims(self, state):
        data = {'raw_text': state['raw_text']}
        if not self.original_input:
            data['translation_zh'] = state.get('translation_zh', '')
        payload = self.call(state, 'split_claims', data, SplitOutput)
        claims, diagnostic = resolve_claims(state['raw_text'], payload['claims'], state['clause_id'])
        complete = diagnostic['coverage_complete']
        return {'claims': [c.model_dump(mode='json') for c in claims], 'coverage_complete': complete,
                'diagnostics': {**state.get('diagnostics', {}), 'split': diagnostic},
                'work_ids': [c.id for c in claims], **({} if complete else {'fatal': 'CLAIM_INCOMPLETE', 'reason_codes': ['CLAIM_INCOMPLETE']})}

    def retrieve(self, state):
        return self._retrieve(state, supplement=False)

    def _retrieve(self, state, supplement):
        ctx = state['context']
        candidates = dict(state.get('candidates_by_claim', {}))
        diagnostics = dict(state.get('retrieval_diagnostics', {}))
        for item in state['claims']:
            if item['id'] not in state['work_ids']:
                continue
            claim = Claim.model_validate(item)
            gap = state.get('assessments_by_claim', {}).get(claim.id, {}).get('explanation', '') if supplement else ''
            query = claim.text
            source_context = ctx.get('source_context', {})
            parameter_search = self.original_input and needs_parameter_value(claim.text, source_context, len(state['claims']))
            source_text = context_query(source_context, include_requirement_values=not parameter_search)
            if source_text:
                query += '\nOriginal sheet context: ' + source_text
            if not self.original_input and is_parameter_requirement(claim.text, source_context):
                query += '\nParameter capacity: prefer Feature List actual supported values, units and applicable conditions.'
            if supplement:
                query += '\nEvidence gap: ' + gap[:600]
                if re.search(r'\b(?:IEEE|IETF|RFC|standard)\b', claim.text, re.I):
                    query += '\nEvidence for the standard and scope explicitly requested; Internet-Draft is not a published RFC.'
            query_claim = claim.model_copy(update={'text': query})
            policy = getattr(self.settings, 'processing', {})
            character_limit = policy.get('evidence_max_characters', 12000)
            options = {'prefer_features': True, 'max_characters': character_limit} if parameter_search else {}
            refs = self.retriever.retrieve(query_claim, ctx['product_id'], ctx['knowledge_snapshot_id'],
                                           supplement=supplement, **options) if ctx.get('knowledge_snapshot_id') else []
            new_refs = [ref.model_dump(mode='json') for ref in refs]
            old = candidates.get(claim.id, []) if supplement else []
            candidates[claim.id], budget = bound_candidates(new_refs, old,
                limit=policy.get('candidate_limit', 8), max_characters=character_limit,
                prefer_features=not self.original_input and is_parameter_requirement(claim.text, source_context),
                partition_features=parameter_search)
            previous = diagnostics.get(claim.id, {})
            diagnostics[claim.id] = {**previous, 'queries': [*previous.get('queries', []),
                {'query': query, 'supplement': supplement, 'gap': gap,
                 **({'feature_list_first': parameter_search} if self.original_input else {}),
                 'candidate_chunk_ids': [r.chunk_id for r in refs], 'budget': budget}],
                'new_selected_chunk_ids': [r['chunk_id'] for r in candidates[claim.id] if r['chunk_id'] not in {v['chunk_id'] for v in old}]}
        return {'candidates_by_claim': candidates, 'retrieval_diagnostics': diagnostics}

    def admit_evidence(self, state):
        admitted = dict(state.get('admitted_by_claim', {}))
        excluded_reasons = dict(state.get('excluded_reasons', {}))
        diagnostics = dict(state.get('retrieval_diagnostics', {}))
        ctx = RunContext.model_validate(state['context'])
        claims = [dict(item) for item in state['claims']]
        for item in claims:
            if item['id'] not in state['work_ids']:
                continue
            refs = [EvidenceRef.model_validate(r) for r in state['candidates_by_claim'].get(item['id'], [])]
            safe = [r for r in refs if not untrusted_instruction(r.quote)]
            project_fact, classification = project_fact_reason(item['text'], item['project_fact'])
            item['project_fact'] = project_fact
            result = admit_evidence(safe, ctx, self.knowledge, project_fact=project_fact, products=self.settings.products, claim_text=item['text'])
            admitted[item['id']] = [r.model_dump(mode='json') for r in result.admitted]
            excluded_reasons[item['id']] = result.reason_codes + (['UNTRUSTED_INPUT'] if len(safe) != len(refs) else [])
            diagnostics[item['id']] = {**diagnostics.get(item['id'], {}), 'admitted_chunk_ids': [r.chunk_id for r in result.admitted],
                'project_fact': project_fact, 'project_fact_classification': classification,
                'excluded_chunk_ids': [r.chunk_id for r in result.excluded], 'rejection_reasons': excluded_reasons[item['id']]}
        return {'claims': claims, 'admitted_by_claim': admitted, 'excluded_reasons': excluded_reasons, 'retrieval_diagnostics': diagnostics}

    def judge_claim(self, state):
        assessments = dict(state.get('assessments_by_claim', {}))
        diagnostics = dict(state.get('retrieval_diagnostics', {}))
        for claim in state['claims']:
            cid = claim['id']
            if cid not in state['work_ids']:
                continue
            source_context = state['context'].get('source_context')
            parameter_required = needs_parameter_value(claim['text'], source_context, len(state['claims']))
            if self.original_input and parameter_required and missing_parameter_threshold(claim['text'], source_context):
                assessments[cid] = ClaimAssessment(claim_id=cid, verdict='UNKNOWN', reason_code='REQUIREMENT_VALUE_MISSING',
                    explanation='No comparison threshold in the clause or mapped requirement values.',
                    gap_type='requirement_input').model_dump(mode='json')
                continue
            evidence = state['admitted_by_claim'].get(cid, [])
            if not evidence:
                reasons = state['excluded_reasons'].get(cid, [])
                assessments[cid] = ClaimAssessment(claim_id=cid, verdict='UNKNOWN', reason_code=reasons[0] if reasons else 'EVIDENCE_MISSING', explanation='No admissible evidence.').model_dump(mode='json')
                continue
            payload = self.call(state, 'judge_claim', {'claim': claim, 'evidence': evidence,
                'parameter_values_required': parameter_required}, JudgeOutput, cid)
            allowed = {e['chunk_id']: e for e in evidence}
            refs, valid = [], True
            for citation in payload['citations']:
                if citation['chunk_id'] not in allowed:
                    valid = False
                    continue
                ref = EvidenceRef.model_validate(allowed[citation['chunk_id']]).model_copy(update={'quote': citation['quote']})
                valid = valid and self.knowledge.verify_reference(ref)
                refs.append(ref)
            if not valid or (payload['verdict'] != 'UNKNOWN' and not refs):
                payload.update(verdict='UNKNOWN', reason_code='CITATION_INVALID')
            if self.original_input and parameter_required and payload['verdict'] == 'UNKNOWN' and (
                payload.get('reason_code') == 'REQUIREMENT_VALUE_MISSING'
                or supplement_reason(payload) == 'GAP_REQUIREMENT_INPUT'
            ):
                payload.update(reason_code='REQUIREMENT_VALUE_MISSING', gap_type='requirement_input')
            supplied_values = payload.get('parameter_values', [])
            values = parameter_facts(supplied_values) if parameter_required else []
            parameter_issues = []
            if not validate_parameter_values(values, refs):
                parameter_issues.append('PARAMETER_VALUE_INVALID')
                # Keep a real missing-threshold/evidence reason on an already unresolved
                # claim. Bad optional facts are discarded and diagnosed independently.
                if payload['verdict'] != 'UNKNOWN':
                    payload.update(verdict='UNKNOWN', reason_code='PARAMETER_VALUE_INVALID')
                values = []
            elif payload['verdict'] != 'UNKNOWN' and parameter_required and not values:
                parameter_issues.append('PARAMETER_VALUE_MISSING')
                payload.update(verdict='UNKNOWN', reason_code='PARAMETER_VALUE_MISSING')
            diagnostics[cid] = {**diagnostics.get(cid, {}), 'parameter_validation': {
                'required': parameter_required, 'ignored_functional_values': len(supplied_values) - len(parameter_facts(supplied_values)) if parameter_required else len(supplied_values),
                'issues': parameter_issues}}
            assessments[cid] = ClaimAssessment(claim_id=cid, verdict=payload['verdict'], reason_code=payload['reason_code'],
                explanation=payload['explanation'], evidence_refs=refs, parameter_values=values,
                gap_type=payload.get('gap_type', 'evidence')).model_dump(mode='json')
        for cid, assessment in assessments.items():
            diagnostics[cid] = {**diagnostics.get(cid, {}), 'remaining_gap': assessment['explanation'] if assessment['verdict'] == 'UNKNOWN' else None,
                                'assessment_reason': assessment['reason_code'], 'supplement_eligibility': supplement_reason(assessment) or 'RETRIEVABLE_EVIDENCE_GAP'}
        return {'assessments_by_claim': assessments, 'retrieval_diagnostics': diagnostics}

    def verify_citations(self, state):
        checks = dict(state.get('checks_by_claim', {}))
        assessments = dict(state['assessments_by_claim'])
        for cid in state['work_ids']:
            assessment = assessments[cid]
            result = []
            for item in assessment['evidence_refs']:
                ref = EvidenceRef.model_validate(item)
                valid = self.knowledge.verify_reference(ref)
                result.append(CitationCheck(ref_id=ref.id, locatable=valid, literal_match=valid,
                    applicable=valid and ref.product_id == state['context']['product_id']).model_dump(mode='json'))
            checks[cid] = result
            if assessment['verdict'] != 'UNKNOWN' and (not result or not all(c['locatable'] and c['literal_match'] and c['applicable'] for c in result)):
                assessments[cid] = {**assessment, 'verdict': 'UNKNOWN', 'reason_code': 'CITATION_INVALID'}
        return {'checks_by_claim': checks, 'assessments_by_claim': assessments}

    def verify_semantic_support(self, state):
        assessments, checks = dict(state['assessments_by_claim']), dict(state['checks_by_claim'])
        for claim in state['claims']:
            cid = claim['id']
            if cid not in state['work_ids'] or assessments[cid]['verdict'] == 'UNKNOWN':
                continue
            assessment = assessments[cid]
            payload = self.call(state, 'verify_semantic_support', {'claim': claim, 'assessment': {k: v for k, v in assessment.items() if k != 'evidence_refs'}, 'evidence': assessment['evidence_refs']}, SemanticOutput, cid)
            checks[cid] = [{**check, 'semantic_support': payload['support']} for check in checks[cid]]
            if payload['support'] != 'SUPPORTED':
                assessments[cid] = {**assessments[cid], 'verdict': 'UNKNOWN', 'reason_code': 'CITATION_INVALID', 'explanation': payload['reason']}
        return {'checks_by_claim': checks, 'assessments_by_claim': assessments}

    def needs_supplement(self, state):
        return not state.get('fatal') and not state.get('supplement_count') and any(supplement_reason(a) is None for a in state.get('assessments_by_claim', {}).values())

    def supplement_retrieve(self, state):
        work_ids = [cid for cid, a in state['assessments_by_claim'].items() if supplement_reason(a) is None]
        output = self._retrieve({**state, 'work_ids': work_ids}, True)
        changed = []
        for cid in work_ids:
            diagnostic = output['retrieval_diagnostics'][cid]
            diagnostic['before_supplement'] = state['assessments_by_claim'][cid]
            if diagnostic['new_selected_chunk_ids']:
                changed.append(cid)
            else:
                diagnostic['supplement_skipped'] = 'NO_NEW_EVIDENCE'
        return {'supplement_count': 1, 'work_ids': changed, **output}

    def aggregate(self, state):
        assessments = [ClaimAssessment.model_validate(state.get('assessments_by_claim', {}).get(c['id'],
            {'claim_id': c['id'], 'verdict': 'UNKNOWN', 'reason_code': state.get('fatal', 'EVIDENCE_MISSING'), 'explanation': 'Step incomplete.'})) for c in state.get('claims', [])]
        complete = state.get('coverage_complete', False) and not state.get('fatal')
        decision = aggregate_claims(assessments, bool(complete))
        reasons = list(dict.fromkeys(state.get('reason_codes', []) + [a.reason_code for a in assessments if a.verdict.value == 'UNKNOWN']))
        if decision.value in {'FC', 'PC'} and is_parameter_requirement(state['raw_text'], state['context'].get('source_context')) and not any(a.parameter_values for a in assessments):
            from app.schemas.core import ClauseDecision
            decision = ClauseDecision.UNKNOWN
            reasons.append('PARAMETER_VALUE_MISSING')
        return {'decision': decision.value, 'assessments': [a.model_dump(mode='json') for a in assessments], 'reason_codes': reasons}

    def render_response(self, state):
        if state['decision'] == 'NC':
            return {'response_en': NC_RESPONSE}
        if state['decision'] == 'UNKNOWN':
            return {'response_en': None}
        claims, assessments = state['claims'], state['assessments']
        ids = {claim['id']: f'SOC_CLAIM_{index}' if self.original_input else claim['id']
               for index, claim in enumerate(claims, 1)}
        if self.original_input:
            claims = [{**_claim_content(c), 'id': ids[c['id']]} for c in claims]
        payload = self.call(state, 'render_response', {'decision': state['decision'],
                             'claims': claims,
                             'assessments': [{**a, 'claim_id': ids[a['claim_id']]} for a in assessments],
                             'product_id': getattr(self.settings,'response_product_name',state['context']['product_id'])}, RenderOutput)
        if set(payload['addressed_claim_ids']) != set(ids.values()):
            return {'fatal': 'OUTPUT_INVALID', 'decision': 'UNKNOWN', 'response_en': None, 'reason_codes': ['OUTPUT_INVALID']}
        gaps = {ids[a['claim_id']] for a in assessments if a['verdict'] == 'CONTRADICTED'}
        if set(payload['gap_sentences']) != gaps or any(not str(v).strip() for v in payload['gap_sentences'].values()):
            return {'fatal': 'OUTPUT_INVALID', 'decision': 'UNKNOWN', 'response_en': None, 'reason_codes': ['OUTPUT_INVALID']}
        text = payload['response_en']
        if gaps:
            text += '\nNot met: ' + ' '.join(payload['gap_sentences'][cid] for cid in sorted(gaps))
        return {'response_en': text + (source_suffix(state['assessments']) if not (self.prompt_bundle or {}).get('response_contract_version') else ''), 'diagnostics': {**state.get('diagnostics', {}),
                'wording': {'body': text, 'source_chunk_ids': list(dict.fromkeys(r['chunk_id'] for a in state['assessments'] for r in a['evidence_refs'])),
                            'characters': len(text), 'words': len(text.split()), 'sentence_preference': 'one_for_simple_FC', 'hard_truncation': False}}}

    def validate_output(self, state):
        result = self._result(state)
        issues = response_issues(result, state['context']['product_id'], self.settings.products)
        valid = not issues
        diagnostic = {'issues': issues, 'generated_response': state.get('response_en'), 'semantic_check': None}
        if valid and state['decision'] in {'FC', 'PC'}:
            checked = self.call(state, 'verify_semantic_support', {'claim': {'text': state['raw_text'], 'claims': state['claims']},
                'assessment': {'verdict': state['decision'], 'response_en': response_body(result)},
                'evidence': [r for a in state['assessments'] for r in a['evidence_refs']]}, SemanticOutput, '__output__')
            valid = checked['support'] == 'SUPPORTED'
            diagnostic['semantic_check'] = checked
            if not valid:
                issues.append('SEMANTIC_UNSUPPORTED')
        diagnostics = {**state.get('diagnostics', {}), 'output_validation': diagnostic}
        if state.get('fatal') or (state.get('decision') != 'UNKNOWN' and not valid):
            return {'decision': 'UNKNOWN', 'response_en': None, 'output_valid': False,
                    'diagnostics': diagnostics,
                    'reason_codes': list(dict.fromkeys(state.get('reason_codes', []) + [state.get('fatal') or 'OUTPUT_INVALID'] +
                        [issue for issue in issues if issue != 'UNKNOWN_NOT_DELIVERABLE']))}
        return {'output_valid': valid, 'diagnostics': diagnostics}

    def _result(self, state):
        return ClauseResult(clause_id=state['clause_id'], raw_text=state['raw_text'], translation_zh=state.get('translation_zh', ''),
            decision=state.get('decision', 'UNKNOWN'), claims=state.get('claims', []), assessments=state.get('assessments', []),
            response_en=state.get('response_en') if state.get('decision') != 'UNKNOWN' else None,
            checks=[c for values in state.get('checks_by_claim', {}).values() for c in values],
            risk_flags=(['KEY_CLAUSE'] if re.search(r'[★*]|mandatory|必须', state['raw_text'], re.I) else []),
            reason_codes=state.get('reason_codes', []), coverage_complete=state.get('coverage_complete', False),
            output_valid=state.get('output_valid', False), candidates=[r for values in state.get('candidates_by_claim', {}).values() for r in values], trace=state.get('trace', []),
            diagnostics={**state.get('diagnostics', {}), 'retrieval': state.get('retrieval_diagnostics', {})})

    def commit_result(self, state):
        result = self._result(state)
        ctx = state['context']
        result_id = str(uuid5(NAMESPACE_URL, ctx['run_id'] + ':' + state['clause_id'] + ':initial'))
        try:
            self.repo.get('clause_result', result_id)
        except DomainError:
            self.repo.put('clause_result', {'result': result.model_dump(mode='json'), 'run_id': ctx['run_id'],
                'clause_id': state['clause_id']}, ctx['project_id'], ctx['run_id'], record_id=result_id)
        for binding in self.repo.list('run_binding', parent_id=ctx['run_id'], limit=100000):
            if binding['clause_id'] == state['clause_id'] and binding.get('result_id') != result_id:
                self.repo.update('run_binding', binding['id'], {'result_id': result_id, 'initial_result_id': result_id, 'review_status': 'pending', 'approved_review_id': None})
        return {'result': result.model_dump(mode='json'), 'result_id': result_id}
