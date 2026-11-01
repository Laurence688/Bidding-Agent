from typing import TypedDict, Any
from app.telemetry.tracing import Recorder
from langsmith import tracing_context
from langgraph.graph import StateGraph, START, END
from app.workflows.nodes import SocNodes
from app.persistence.checkpoints import sqlite_checkpointer
from app.schemas.core import ClauseResult


class SocState(TypedDict, total=False):
    raw_text: str
    clause_id: str
    context: dict
    translation_zh: str
    claims: list
    work_ids: list
    candidates_by_claim: dict
    admitted_by_claim: dict
    excluded_reasons: dict
    assessments_by_claim: dict
    checks_by_claim: dict
    supplement_count: int
    assessments: list
    decision: str
    response_en: str | None
    coverage_complete: bool
    output_valid: bool
    reason_codes: list
    fatal: str | None
    trace: list
    result: dict
    result_id: str
    diagnostics: dict
    retrieval_diagnostics: dict


class SocWorkflow:
    def __init__(self, repo, settings, gateway, retriever, knowledge, prompt_bundle=None):
        self.repo, self.settings = repo, settings
        self.nodes = SocNodes(repo, settings, gateway, retriever, knowledge, prompt_bundle)

    def build(self, saver):
        graph = StateGraph(SocState)
        sequence = ['validate_input', 'translate', 'split_claims', 'retrieve', 'admit_evidence', 'judge_claim', 'verify_citations', 'verify_semantic_support']
        ending = ['aggregate', 'render_response', 'validate_output', 'commit_result']
        for name in [*sequence, 'supplement_retrieve', *ending]:
            graph.add_node(name, lambda state, node=name: self.nodes.run_node(node, state))
        graph.add_edge(START, sequence[0])
        for left, right in zip(sequence, sequence[1:]):
            graph.add_edge(left, right)
        graph.add_conditional_edges('verify_semantic_support', lambda s: 'supplement' if self.nodes.needs_supplement(s) else 'aggregate',
                                    {'supplement': 'supplement_retrieve', 'aggregate': 'aggregate'})
        graph.add_edge('supplement_retrieve', 'admit_evidence')
        for left, right in zip(ending, ending[1:]):
            graph.add_edge(left, right)
        graph.add_edge('commit_result', END)
        return graph.compile(checkpointer=saver)

    def run_clause(self, clause_id, raw_text, context):
        metadata = {k: getattr(context, k) for k in ('run_id', 'mode', 'product_id', 'config_snapshot_id', 'knowledge_snapshot_id', 'evaluation_run_id', 'session_id', 'task_id')}
        metadata['clause_id'] = clause_id
        with tracing_context(enabled=False), Recorder(self.repo, self.settings).span(
            context.run_id + ':' + clause_id, 'SOC clause',
            {'raw_text': raw_text, 'source_context': context.source_context, 'lifecycle_context': context.lifecycle_context.model_dump(mode='json')}, metadata=metadata) as span:
            result = self._run_clause(clause_id, raw_text, context)
            if hasattr(span, 'payload'):
                from uuid import uuid5, NAMESPACE_URL
                span.payload['extra']['metadata']['result_id'] = str(uuid5(NAMESPACE_URL, context.run_id + ':' + clause_id + ':initial'))
            span.finish(result.model_dump(mode='json'))
            return result

    def _run_clause(self, clause_id, raw_text, context):
        initial = {'raw_text': raw_text, 'clause_id': clause_id, 'context': context.model_dump(mode='json'),
                   'fatal': None, 'reason_codes': [], 'trace': [], 'claims': [], 'work_ids': [], 'supplement_count': 0,
                   'candidates_by_claim': {}, 'admitted_by_claim': {}, 'excluded_reasons': {}, 'assessments_by_claim': {},
                   'checks_by_claim': {}, 'assessments': [], 'coverage_complete': False, 'output_valid': False,
                   'response_en': None, 'decision': 'UNKNOWN', 'translation_zh': '', 'result': {}, 'result_id': '',
                   'diagnostics': {}, 'retrieval_diagnostics': {}}
        with sqlite_checkpointer(self.repo.data_dir) as saver:
            result = self.build(saver).invoke(initial, {'configurable': {'thread_id': context.run_id + ':' + clause_id}, 'recursion_limit': 40})
        return ClauseResult.model_validate(result['result'])
