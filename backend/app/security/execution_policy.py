"""Frozen task intent intersected with current, fail-closed outbound permissions."""
import copy
import json
from urllib.parse import urlsplit

from app.domain.errors import DomainError
from app.model_gateway.endpoints import is_local_endpoint
from app.security.configuration import configuration_lock, atomic_write


class ExecutionSettings:
    def __init__(self, data_dir):
        from pathlib import Path
        self.root = Path(data_dir)
        self.path = self.root / 'settings' / 'execution.json'

    def view(self):
        if not self.path.exists():
            return {'version': 0, 'model_mode': 'cloud', 'network_allowed': False}
        try:
            result = json.loads(self.path.read_text('utf-8'))
            if result['model_mode'] not in {'local', 'cloud'} or type(result['network_allowed']) is not bool or type(result['version']) is not int:
                raise ValueError()
            return result
        except (ValueError, KeyError, TypeError):
            raise DomainError('EXECUTION_CONFIG_INVALID', '运行设置损坏，已阻止外部发送', 503)

    def save(self, body):
        if (body.get('model_mode') not in {'local', 'cloud'} or type(body.get('network_allowed')) is not bool
            or type(body.get('expected_version')) is not int
            or set(body) - {'expected_version', 'model_mode', 'network_allowed'}):
            raise DomainError('EXECUTION_CONFIG_INVALID', '请明确指定本地/云端模式及联网权限', 422)
        with configuration_lock(self.root):
            current = self.view()
            if current['version'] != body['expected_version']:
                raise DomainError('VERSION_CONFLICT', '运行设置已变化，请刷新', 409)
            value = {'version': current['version'] + 1, 'model_mode': body['model_mode'], 'network_allowed': body['network_allowed']}
            atomic_write(self.path, json.dumps(value, ensure_ascii=False))
            return value


def assert_endpoint_allowed(policy, endpoint, purpose):
    from app.model_gateway.endpoints import validate_endpoint
    validate_endpoint(endpoint)
    if purpose not in {'generation', 'embedding', 'connection_check', 'sync', 'download'}:
        raise DomainError('EXECUTION_PURPOSE_INVALID', '未知联网用途', 422)
    if purpose=='embedding' and policy.get('embedding_local_only') and not is_local_endpoint(endpoint):
        raise DomainError('LOCAL_INDEX_REQUIRED','当前配置只允许本地 Embedding',409)
    if is_local_endpoint(endpoint):
        return
    if policy.get('model_mode') != 'cloud' or policy.get('network_allowed') is not True:
        message = '本次任务未向外部模型发送内容，已有任务与结果仍保留。请在文本模型设置中允许生成 API 联网。'
        if policy.get('blocked_by_snapshot'):
            message = '此任务创建时未允许联网，原配置快照仍受限制；请允许联网后重新创建任务。已有任务与结果仍保留。'
        raise DomainError('EXECUTION_OFFLINE', message, 409)
    parsed = urlsplit(endpoint)
    for allowed in policy.get('allowed_services', {}).get(purpose, []):
        base = urlsplit(allowed)
        prefix = base.path.rstrip('/')
        if (parsed.scheme == base.scheme == 'https' and parsed.netloc == base.netloc
            and (parsed.path == prefix or parsed.path.startswith(prefix + '/'))):
            return
    raise DomainError('EXECUTION_ENDPOINT_DENIED', '此运行未明确允许该服务或用途', 403)


def policy_for(settings, model_mode=None, frozen=None, extra_services=None):
    current = ExecutionSettings(settings.data_dir).view()
    if frozen is not None:
        result = copy.deepcopy(frozen)
        result['blocked_by_snapshot'] = not bool(result.get('network_allowed')) and bool(current['network_allowed'])
        result['network_allowed'] = bool(result.get('network_allowed') and current['network_allowed'])
        return result
    mode = model_mode or current['model_mode']
    if mode not in {'local', 'cloud'}:
        raise DomainError('EXECUTION_CONFIG_INVALID', '运行模式无效', 422)
    from app.model_gateway.profiles import selected_models
    effective = selected_models(settings.models) if mode=='cloud' else settings.models
    endpoints = [p['base_url'] for p in effective.get('providers', {}).values() if p]
    services = {'generation': endpoints, 'connection_check': endpoints}
    if extra_services:
        services.update(extra_services)
    return {'model_mode': mode, 'network_allowed': current['network_allowed'], 'settings_version': current['version'], 'allowed_services': services,
            'embedding_local_only':bool(effective.get('embedding_local_only'))}


def models_for(settings, mode):
    if mode == 'cloud':
        from app.model_gateway.profiles import selected_models
        return selected_models(settings.models)
    timeout = settings.local_models.get('generation_timeout_seconds', 180)
    if type(timeout) is not int or not 1 <= timeout <= 600:
        raise DomainError('LOCAL_MODEL_CONFIG_INVALID', '本地模型单次限时须为 1 至 600 秒的整数', 422)
    provider = {'provider': 'llama_cpp', 'base_url': 'http://127.0.0.1:8766/v1',
                'model_id': 'Qwen3-4B-Instruct-2507', 'api_key_env': 'LAURA_LOCAL_API_KEY',
                'timeout': timeout, 'max_output_tokens': 4096, 'context_limit': 8192,
                'structured_output_mode': 'json_schema', 'sdk_max_retries': 0}
    from app.local_runtime.configuration import LocalRuntimeSettings
    provider.update(LocalRuntimeSettings(settings).frozen_provider())
    result = {**copy.deepcopy(settings.models), 'providers': {'primary': provider}}
    for node, local_policy in settings.local_models.get('node_policies', {}).items():
        policies = result.setdefault('node_policies', {})
        policies[node] = {**policies.get(node, {}), **copy.deepcopy(local_policy)}
    return result
