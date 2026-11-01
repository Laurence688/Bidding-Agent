from urllib.parse import urlparse
from app.model_gateway.endpoints import is_local_endpoint
import httpx
from app.model_gateway.contracts import ProviderFailure


class ChatCompletionsAdapter:
    def __init__(self, secrets, policy=None, data_dir=None):
        self.secrets = secrets
        self.policy = policy
        self.data_dir = data_dir

    def __call__(self, provider, messages, schema):
        if provider.get('provider') == 'llama_cpp':
            if not provider.get('artifact_identity') or not self.data_dir:
                raise ProviderFailure('LOCAL_MODEL_MANIFEST_MISSING')
            from app.rag.resource_pool import heavy_lease
            with heavy_lease(self.data_dir, 'local_generation'):
                return self.managed_call(provider, messages, schema)
        key = None if provider.get('key_disabled') else self.secrets.get(provider['api_key_env'])
        url = provider.get('base_url', '').rstrip('/')
        if (not key and not is_local_endpoint(url)) or not provider.get('model_id') or not (urlparse(url).scheme=='https' or is_local_endpoint(url)):
            raise ProviderFailure('MODEL_CONFIG_MISSING')
        payload = {'model': provider['model_id'], 'messages': messages,
                   'max_tokens': provider['max_output_tokens'], 'stream': False}
        if provider.get('structured_output_mode') == 'json_object':
            payload['response_format'] = {'type': 'json_object'}
        if 'temperature' in provider:
            payload['temperature'] = provider['temperature']
        if urlparse(url).hostname=='api.deepseek.com' and provider.get('provider') == 'deepseek' and provider.get('thinking_mode') in {'enabled', 'disabled'}:
            payload['thinking'] = {'type': provider['thinking_mode']}
        if urlparse(url).hostname=='open.bigmodel.cn' and provider.get('provider')=='glm' and provider.get('thinking_mode') in {'enabled','disabled'}:
            payload['thinking']={'type':provider['thinking_mode']}
        try:
            # HTTPX transport 的重试为零；所有预算与退避仅由网关负责。
            with httpx.Client(timeout=provider['timeout'], transport=httpx.HTTPTransport(retries=0), follow_redirects=False, trust_env=False) as client:
                if self.policy is not None:
                    from app.security.execution_policy import assert_endpoint_allowed
                    assert_endpoint_allowed(self.policy() if callable(self.policy) else self.policy, url, 'generation')
                response = client.post(url + '/chat/completions', json=payload, headers={'Authorization': f'Bearer {key}'} if key else {})
            if response.status_code in (401, 403):
                raise ProviderFailure('AUTH_FAILED')
            if response.status_code == 429 or response.status_code >= 500:
                raise ProviderFailure(f'HTTP_{response.status_code}', True)
            if response.status_code != 200:
                raise ProviderFailure('PROVIDER_CONFIG_ERROR')
            return response.json()
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ProviderFailure('MODEL_TIMEOUT' if isinstance(exc, httpx.TimeoutException) else 'MODEL_CONNECTION', True) from exc
        except ValueError as exc:
            raise ProviderFailure('OUTPUT_INVALID') from exc

    def managed_call(self, provider, messages, schema):
        import json
        from pathlib import Path
        from app.local_runtime.manager import LocalModelManager
        from app.domain.identifiers import content_hash
        path = Path(provider['manifest_path'])
        if not path.is_file() or content_hash(json.loads(path.read_text('utf-8'))) != provider['artifact_identity']:
            raise ProviderFailure('ARTIFACT_IDENTITY_CHANGED')
        manager = LocalModelManager(self.data_dir, provider['model_root'])
        state = manager.start(path)
        api_key = manager.api_key_path.read_text('utf-8').strip()
        local_provider = {**provider, 'base_url': f'http://127.0.0.1:{state["port"]}/v1'}
        try:
            result = LocalLlamaAdapter(policy=self.policy, data_dir=self.data_dir, api_key=api_key)(local_provider, messages, schema)
            result['local_runtime'] = {'cold_load_seconds': 0 if state.get('reused') else state.get('cold_start_seconds'),
                                       'instance_id': state.get('instance_id'), 'artifact_identity': state.get('artifact_identity')}
            return result
        finally:
            manager.touch()

    def mark_success(self, provider, raw, schema_hash):
        if provider.get('provider') != 'llama_cpp' or not self.data_dir:
            return False
        from app.local_runtime.manager import LocalModelManager
        runtime = raw.get('local_runtime') or {}
        return LocalModelManager(self.data_dir, provider['model_root']).record_generation(
            runtime.get('instance_id'), runtime.get('artifact_identity'), schema_hash)


class LocalLlamaAdapter:
    """Fixed llama.cpp native protocol: template once, count and send identical IDs."""
    def __init__(self, client_factory=None, policy=None, data_dir=None, api_key=None):
        self.client_factory = client_factory or httpx.Client
        self.policy = policy
        self.data_dir = data_dir
        self.api_key = api_key

    def __call__(self, provider, messages, schema):
        from contextlib import nullcontext
        from app.rag.resource_pool import heavy_lease
        lease = heavy_lease(self.data_dir, 'generation') if self.data_dir else nullcontext()
        with lease:
            return self.generate(provider, messages, schema)

    def generate(self, provider, messages, schema):
        import time
        from urllib.parse import urlsplit, urlunsplit
        if not is_local_endpoint(provider.get('base_url', '')):
            raise ProviderFailure('LOCAL_ENDPOINT_REQUIRED')
        parsed = urlsplit(provider['base_url'])
        base = urlunsplit((parsed.scheme, parsed.netloc, '', '', ''))
        deadline = time.monotonic() + min(provider['timeout'], 600)
        def post(client, path, body):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProviderFailure('MODEL_TIMEOUT', True)
            if self.policy is not None:
                from app.security.execution_policy import assert_endpoint_allowed
                assert_endpoint_allowed(self.policy() if callable(self.policy) else self.policy, base, 'generation')
            response = client.post(base + path, json=body, timeout=remaining)
            if response.status_code in {429, 503}:
                raise ProviderFailure('LOCAL_MODEL_NOT_READY', True)
            if response.status_code != 200:
                raise ProviderFailure('LOCAL_PROTOCOL_INVALID')
            data = response.json()
            if not isinstance(data, dict):
                raise ProviderFailure('LOCAL_PROTOCOL_INVALID')
            return data
        try:
            with self.client_factory(trust_env=False, follow_redirects=False,
                                     headers={'Authorization': 'Bearer ' + self.api_key} if self.api_key else {}) as client:
                formatted = post(client, '/apply-template', {'messages': messages})
                if not isinstance(formatted.get('prompt'), str):
                    raise ProviderFailure('LOCAL_PROTOCOL_INVALID')
                tokenized = post(client, '/tokenize', {'content': formatted['prompt'], 'add_special': True, 'parse_special': True})
                tokens = tokenized.get('tokens')
                if not isinstance(tokens, list) or not tokens or any(type(t) is not int for t in tokens):
                    raise ProviderFailure('LOCAL_PROTOCOL_INVALID')
                context = {'input_tokens': len(tokens), 'reserved_output_tokens': provider['max_output_tokens'],
                           'context_limit': provider['context_limit'], 'exact': True}
                if len(tokens) + provider['max_output_tokens'] > provider['context_limit']:
                    raise ProviderFailure('CONTEXT_LIMIT', local_context=context)
                result = post(client, '/completion', {'prompt': tokens, 'n_predict': provider['max_output_tokens'],
                              'json_schema': schema, 'stream': False, 'cache_prompt': True,
                              'temperature': provider.get('temperature', 0), 'seed': 0})
                finish = 'length' if result.get('stopped_limit') else ('stop' if result.get('stop') else 'invalid')
                output_tokens = result.get('tokens_predicted')
                return {'choices': [{'message': {'content': result.get('content')}, 'finish_reason': finish}],
                        'usage': {'prompt_tokens': result.get('tokens_evaluated', len(tokens)),
                                  'completion_tokens': output_tokens,
                                  'total_tokens': len(tokens) + output_tokens if type(output_tokens) is int else None},
                        'local_context': context, 'timings': result.get('timings')}
        except httpx.TimeoutException as exc:
            raise ProviderFailure('MODEL_TIMEOUT', True) from exc
        except httpx.NetworkError as exc:
            raise ProviderFailure('MODEL_CONNECTION', True) from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise ProviderFailure('LOCAL_PROTOCOL_INVALID') from exc
