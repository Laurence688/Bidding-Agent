from app.model_gateway.endpoints import is_local_endpoint
from app.telemetry.tracing import child_span
import json
import time
from pydantic import ValidationError
from app.domain.identifiers import new_id, canonical, content_hash, utc_now
from app.model_gateway.contracts import ModelResult, ProviderFailure
from app.model_gateway.adapters import ChatCompletionsAdapter
from app.model_gateway.usage import normalize_usage
from app.security.redaction import redact
from app.model_gateway.diagnostics import failure_category
from app.domain.errors import DomainError

RETRYABLE_OUTPUT_ERRORS = {'OUTPUT_TRUNCATED', 'OUTPUT_INVALID'}


class ModelGateway:
    def __init__(self, repo, config, secrets, transport=None, sleeper=time.sleep, policy=None):
        self.repo, self.config, self.secrets = repo, config, secrets
        self.transport = transport or ChatCompletionsAdapter(secrets, policy=policy, data_dir=repo.data_dir)
        self.sleeper = sleeper
        self.policy = policy
        self.secret_values = [value for key, value in secrets.items() if key.endswith('_API_KEY') and value]

    def provider_for(self, request, count):
        candidate = self.config['providers'].get('primary' if count < 2 else 'backup')
        if not candidate:
            return None
        provider = dict(candidate)
        if provider['provider'] == 'deepseek' and request.logical_node_name in {'translate', 'split_claims'}:
            provider['thinking_mode'] = provider.get('structured_thinking', 'auto')
        policy = self.config.get('node_policies', {}).get(request.logical_node_name, {})
        if provider['provider'] == 'deepseek' and policy.get('thinking_mode') in {'auto', 'disabled', 'enabled'}:
            provider['thinking_mode'] = policy['thinking_mode']
        if isinstance(policy.get('max_output_tokens'), int) and 256 <= policy['max_output_tokens'] <= provider['max_output_tokens']:
            provider['max_output_tokens'] = policy['max_output_tokens']
        if request.max_output_tokens is not None:
            provider['max_output_tokens'] = min(provider['max_output_tokens'], max(1, request.max_output_tokens))
        return provider

    def budget_exhausted(self, request):
        limit = request.max_attempts or (2 if self.config.get('active_generation_profile_id') else 3)
        if len(self.attempts(request)) >= limit:
            return True
        if request.operation_budget_id and request.operation_max_attempts:
            return sum(a.get('operation_budget_id') == request.operation_budget_id for a in self.attempts(run_id=request.run_id)) >= request.operation_max_attempts
        return False

    def record_context(self, request, provider, input_tokens=None, record_id=None):
        from app.services.context_management import request_budget
        value=request_budget(request.messages, request.output_model.model_json_schema(), provider)
        if input_tokens is not None:
            value.update(input_tokens=input_tokens, total_tokens=input_tokens+provider['max_output_tokens'], counting='exact')
            value['ratio']=value['total_tokens']/value['window'] if value['window'] else None
        value.update(logical_node_name=request.logical_node_name, clause_id=request.clause_id, claim_id=request.claim_id,
                     config_snapshot_id=request.config_version, run_id=request.run_id)
        if record_id is None:value.update(status='assembled',sent=False)
        with self.repo.transaction():
            previous=[r for r in self.repo.list('request_context',limit=100000) if r.get('run_id')==request.run_id and r['id']!=record_id]
            value['peak_tokens']=max([r.get('total_tokens') or 0 for r in previous]+[value.get('total_tokens') or 0])
            if record_id:return self.repo.update('request_context',record_id,value)
            try:parent=self.repo.get('run',request.run_id)
            except DomainError:
                try:parent=self.repo.get('job',request.run_id)
                except DomainError:parent=None
            return self.repo.put('request_context',value,project_id=parent['project_id'] if parent else None,parent_id=parent['id'] if parent else None)

    def attempts(self, request=None, run_id=None):
        sql, params = 'SELECT * FROM model_attempts WHERE 1=1', []
        if request:
            sql += ' AND run_id=? AND clause_id=? AND claim_id=? AND logical_node_name=?'
            params.extend([request.run_id, request.clause_id, request.claim_id, request.logical_node_name])
        elif run_id:
            sql += ' AND run_id=?'
            params.append(run_id)
        with self.repo.connection() as db:
            return [{**dict(row), **json.loads(row['data'])} for row in db.execute(sql + ' ORDER BY rowid', params)]

    def reserve_attempt(self, request, role=None, invocation_id=None, invocation_attempt=1, retry_of=None, expected_count=None):
        with self.repo.transaction() as db:
            count = len(self.attempts(request))
            if expected_count is not None and count != expected_count:
                return None
            if self.budget_exhausted(request):
                return None
            # 请求哈希仅用于审计；不能加入预算主键，否则补检索会刷新预算。
            role = 'primary' if count < 2 else 'backup'
            provider = self.provider_for(request, count)
            if provider is None:
                return None
            if retry_of:
                already_retried = db.execute("""SELECT 1 FROM model_attempts WHERE run_id=? AND clause_id=?
                    AND json_extract(data,'$.retry_of_attempt_id') IS NOT NULL LIMIT 1""",
                    (request.run_id, request.clause_id)).fetchone()
                if already_retried or (provider['provider'], provider['model_id'], role) != (
                    retry_of['provider'], retry_of['model'], retry_of['role']
                ) or provider['max_output_tokens'] != retry_of['provider_settings']['max_output_tokens']:
                    return None
            ident = new_id()
            data = {'started_at': utc_now(), 'role': role, 'messages': redact(request.messages, self.secret_values),
                    'invocation_id': invocation_id or new_id(), 'invocation_attempt': invocation_attempt, 'call_kind': request.call_kind,
                    'operation_budget_id': request.operation_budget_id,
                    'schema_hash': content_hash(request.output_model.model_json_schema()), 'config_version': request.config_version,
                    'usage': None, 'latency': None,
                    'provider_settings': {key: provider.get(key) for key in ['thinking_mode', 'max_output_tokens', 'timeout']}}
            if retry_of:
                data['retry_of_attempt_id'] = retry_of['attempt_id']
            db.execute('INSERT INTO model_attempts(attempt_id,run_id,clause_id,claim_id,logical_node_name,attempt_index,provider,model,request_hash,status,data) VALUES(?,?,?,?,?,?,?,?,?,?,?)', (ident, request.run_id, request.clause_id,
                request.claim_id, request.logical_node_name, count + 1, provider['provider'], provider['model_id'],
                content_hash(request.messages), 'in_flight', canonical(data)))
            return {'attempt_id': ident, 'provider': provider, 'index': count + 1, 'data': data}

    def invoke(self, request):
        identifiers = []
        invocation_id = new_id()
        schema = request.output_model.model_json_schema()
        request_hash = content_hash(request.messages) if request.retry_invalid_output else None
        schema_hash = content_hash(schema) if request.retry_invalid_output else None
        while True:
            attempts = self.attempts(request)
            count = len(attempts)
            retry_of = None
            invocation_attempt = len(identifiers) + 1
            if request.retry_invalid_output:
                matching = [a for a in attempts if a['request_hash'] == request_hash
                            and a.get('schema_hash') == schema_hash
                            and a.get('config_version') == request.config_version
                            and a.get('call_kind') == request.call_kind]
                if matching:
                    previous = matching[-1]
                    if previous['status'] == 'succeeded':
                        try:
                            payload = request.output_model.model_validate(previous['validated_payload']).model_dump(mode='json')
                        except (ValueError, KeyError, IndexError, TypeError):
                            return ModelResult(error_code='OUTPUT_INVALID', attempt_ids=[previous['attempt_id']])
                        return ModelResult(payload=payload, attempt_ids=[previous['attempt_id']], stop_reason=previous['stop_reason'])
                    if previous['status'] == 'in_flight':
                        return ModelResult(error_code='ATTEMPT_INTERRUPTED', attempt_ids=[previous['attempt_id']])
                    code = previous.get('error_code')
                    if code in RETRYABLE_OUTPUT_ERRORS and not previous.get('retry_of_attempt_id'):
                        retry_of = previous
                    elif previous.get('diagnostics', {}).get('retry_policy') != 'bounded_transient_only':
                        return ModelResult(error_code=code or 'ATTEMPT_INTERRUPTED', attempt_ids=[previous['attempt_id']])
                    invocation_id = previous['invocation_id']
                    invocation_attempt = previous['invocation_attempt'] + 1
            if count>=2 and self.config.get('active_generation_profile_id'):
                return ModelResult(error_code=attempts[-1].get('error_code') or 'ATTEMPT_BUDGET_EXHAUSTED',attempt_ids=identifiers)
            if self.budget_exhausted(request):
                return ModelResult(error_code=retry_of['error_code'] if retry_of else 'ATTEMPT_BUDGET_EXHAUSTED', attempt_ids=identifiers)
            if count >= 3:
                return ModelResult(error_code='ATTEMPT_BUDGET_EXHAUSTED', attempt_ids=identifiers)
            if retry_of and count >= 2 and retry_of['role'] == 'primary':
                return ModelResult(error_code=retry_of['error_code'], attempt_ids=identifiers)
            provider = self.provider_for(request, count)
            if provider is None:
                return ModelResult(error_code=retry_of['error_code'] if retry_of else 'NO_ALLOWED_BACKUP', attempt_ids=identifiers)
            if self.policy is not None:
                from app.security.execution_policy import assert_endpoint_allowed
                try:
                    assert_endpoint_allowed(self.policy() if callable(self.policy) else self.policy, provider['base_url'], 'generation')
                except Exception as exc:
                    return ModelResult(error_code=getattr(exc, 'code', 'EXECUTION_POLICY_ERROR'), attempt_ids=identifiers)
            request_context=self.record_context(request,provider)
            estimated = request_context['total_tokens']
            if provider.get('provider') != 'llama_cpp' and estimated > provider['context_limit']:
                self.repo.update('request_context',request_context['id'],{'status':'blocked','error_code':'CONTEXT_LIMIT'})
                return ModelResult(error_code='CONTEXT_LIMIT', attempt_ids=identifiers)
            if (provider.get('key_disabled') or not self.secrets.get(provider['api_key_env'])) and not is_local_endpoint(provider.get('base_url','')):
                self.repo.update('request_context',request_context['id'],{'status':'blocked','error_code':'MODEL_CONFIG_MISSING'})
                return ModelResult(error_code='MODEL_CONFIG_MISSING', attempt_ids=identifiers)
            # 不让配置中的凭据意外出现在发送数据中。
            safe_messages = redact(request.messages, self.secret_values)
            reserved = self.reserve_attempt(request, invocation_id=invocation_id, invocation_attempt=invocation_attempt,
                retry_of=retry_of, expected_count=count if request.retry_invalid_output else None)
            if reserved is None:
                self.repo.update('request_context', request_context['id'], {'status': 'blocked', 'error_code': 'ATTEMPT_BUDGET_EXHAUSTED'})
                return ModelResult(error_code=retry_of['error_code'] if retry_of else 'ATTEMPT_BUDGET_EXHAUSTED', attempt_ids=identifiers)
            ident, provider = reserved['attempt_id'], reserved['provider']
            self.repo.update('request_context',request_context['id'],{'status':'in_flight','sent':True,'attempt_id':ident})
            identifiers.append(ident)
            started, raw, code, transient, stop, payload = time.monotonic(), None, None, False, None, None
            output_text, schema_errors, json_valid = '', [], False
            local_context = None
            try:
                with child_span(ident, request.logical_node_name + ' model attempt', {'messages': safe_messages}, 'llm',
                    {'ls_provider': provider['provider'], 'ls_model_name': provider['model_id'], 'attempt_index': reserved['index'],
                     'claim_id': request.claim_id, 'call_kind': request.call_kind, 'role': reserved['data']['role']}) as span:
                    raw = self.transport(provider, safe_messages, schema)
                    usage = normalize_usage(raw.get('usage'), provider['provider'])
                    span.finish({'choices': [{'message': {'role': c.get('message', {}).get('role', 'assistant'), 'content': c.get('message', {}).get('content')},
                        'finish_reason': c.get('finish_reason')} for c in raw.get('choices', [])],
                        'usage_metadata': {k: v for k, v in usage.items() if k in {'input_tokens', 'output_tokens', 'total_tokens'} and v is not None},
                        'usage_complete': usage['complete']})
                    choice = raw['choices'][0]
                    content = choice.get('message', {}).get('content')
                    output_text = content if isinstance(content, str) else ''
                    stop = choice.get('finish_reason')
                    if stop not in ('stop', 'end_turn'):
                        raise ProviderFailure('OUTPUT_TRUNCATED' if stop == 'length' else 'OUTPUT_STOP_INVALID')
                    parsed = json.loads(choice['message']['content'])
                    json_valid = True
                    payload = request.output_model.model_validate(parsed).model_dump(mode='json')
            except ProviderFailure as exc:
                code, transient = exc.code, exc.transient
                local_context = exc.local_context
            except DomainError as exc:
                code = exc.code
            except ValidationError as exc:
                code = 'OUTPUT_INVALID'
                schema_errors = [{'path': '.'.join(map(str, e['loc'])), 'type': e['type']} for e in exc.errors(include_input=False, include_url=False)]
            except (KeyError, IndexError, TypeError, ValueError):
                code = 'OUTPUT_INVALID'
            except Exception:
                code = 'PROVIDER_UNEXPECTED_ERROR'
            data = {**reserved['data'], 'ended_at': utc_now(), 'latency': time.monotonic() - started,
                'usage': normalize_usage(raw.get('usage') if isinstance(raw, dict) else None, provider['provider']),
                'response': redact(raw, self.secret_values), 'error_code': code, 'stop_reason': stop,
                'diagnostics': {'output_characters': len(output_text), 'json_valid': json_valid, 'schema_valid': payload is not None,
                                'schema_errors': schema_errors, 'failure_category': failure_category(code),
                                'retry_policy': 'soc_output_retry' if retry_of else 'bounded_transient_only' if transient else
                                'soc_output_retry_eligible' if request.retry_invalid_output and code in RETRYABLE_OUTPUT_ERRORS else 'no_automatic_retry'},
                'context_estimate': estimated if provider.get('provider') != 'llama_cpp' else None,
                'context_estimate_is_estimate': provider.get('provider') != 'llama_cpp',
                'local_context': raw.get('local_context') if isinstance(raw, dict) else local_context}
            safe_payload = redact(payload, self.secret_values)
            if request.retry_invalid_output and code is None:
                data['validated_payload'] = safe_payload
            with self.repo.transaction() as db:
                db.execute('UPDATE model_attempts SET status=?,data=? WHERE attempt_id=?', ('failed' if code else 'succeeded', canonical(data), ident))
                usage=data['usage']
                self.record_context(request,provider,usage.get('input_tokens'),request_context['id'])
                self.repo.update('request_context',request_context['id'],{'status':'failed' if code else 'succeeded','error_code':code})
            if code is None:
                marker = getattr(self.transport, 'mark_success', None)
                if marker is not None:
                    try:
                        marker(provider, raw, content_hash(schema))
                    except (DomainError, OSError, ValueError):
                        # A readiness display write must not turn valid model output into failure.
                        pass
                return ModelResult(payload=safe_payload, attempt_ids=identifiers, stop_reason=stop)
            if request.retry_invalid_output and code in RETRYABLE_OUTPUT_ERRORS and not retry_of:
                continue
            if retry_of:
                return ModelResult(error_code=code, attempt_ids=identifiers, stop_reason=stop)
            if not transient:
                return ModelResult(error_code=code, attempt_ids=identifiers, stop_reason=stop)
            self.sleeper(min(0.25 * reserved['index'], 1.0))
