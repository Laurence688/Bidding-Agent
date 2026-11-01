"""Independent generation profiles; legacy provider roles remain intact."""
import copy
from app.domain.errors import DomainError

IDS = ('glm', 'deepseek', 'doubao', 'internal')


def factory_profiles():
    """Fresh release defaults, independent of local settings and environment overlays."""
    return {
        'glm': {'provider':'glm','base_url':'https://open.bigmodel.cn/api/paas/v4',
                'model_id':'glm-4.7-flash','api_key_env':'GLM_API_KEY','config_env_prefix':'GLM',
                'timeout':180,'max_output_tokens':4096,'context_limit':200000,
                'structured_output_mode':'json_object','structured_thinking':'disabled','sdk_max_retries':0},
        'deepseek': {'provider':'deepseek','base_url':'https://api.deepseek.com',
                'model_id':'deepseek-flash','api_key_env':'DEEPSEEK_API_KEY','api_protocol':'chat_completions',
                'timeout':90,'max_output_tokens':4096,'context_limit':32000,
                'structured_output_mode':'json_object','structured_thinking':'disabled','sdk_max_retries':0},
        'doubao': {'provider':'doubao','base_url':'https://ark.cn-beijing.volces.com/api/v3',
                'model_id':'doubao-seed-2-1-pro-260915','api_key_env':'ARK_API_KEY','api_protocol':'chat_completions',
                'timeout':90,'max_output_tokens':4096,'context_limit':32000,
                'structured_output_mode':'json_object','sdk_max_retries':0},
        'internal': {'provider':'openai_compatible','base_url':'','model_id':'',
                'api_key_env':'INTERNAL_API_KEY','config_env_prefix':'INTERNAL',
                'timeout':90,'max_output_tokens':4096,'context_limit':32000,
                'structured_output_mode':'json_object','sdk_max_retries':0}}


def profiles_for(models):
    defaults=factory_profiles()
    defaults.update(copy.deepcopy(models.get('generation_profiles', {})))
    result={'glm':defaults['glm'],'deepseek':copy.deepcopy(models['providers']['primary']),
            'doubao':copy.deepcopy(models['providers']['backup']),'internal':defaults['internal']}
    # Only completely empty public presets can be filled. Custom endpoints/IDs and
    # environment overlays are never replaced or persisted by this read.
    for ident in ('glm','deepseek','doubao'):
        if not result[ident].get('base_url') and not result[ident].get('model_id'):
            result[ident].update({k:factory_profiles()[ident][k] for k in ('base_url','model_id')})
    return result


def selected_models(models):
    ident=models.get('active_generation_profile_id')
    if ident is None:
        return copy.deepcopy(models)
    if ident not in IDS:
        raise DomainError('MODEL_CONFIG_INVALID','生成供应商选择无效',422)
    provider=copy.deepcopy(profiles_for(models)[ident])
    if not provider.get('base_url') or not provider.get('model_id'):
        raise DomainError('MODEL_CONFIG_MISSING','请先填写所选供应商的 API 地址和模型 ID',409)
    if provider['provider']=='glm':
        provider['thinking_mode']=provider.get('structured_thinking','disabled')
    return {'config_version':models['config_version'], 'providers':{'primary':provider},
            'node_policies':copy.deepcopy(models.get('node_policies',{})),
            'active_generation_profile_id':ident,'embedding_local_only':True}
