"""Fresh CAS model configuration writes, serialized with all .env writers."""
import json
from urllib.parse import urlparse
import yaml
from dotenv import dotenv_values
from app.domain.errors import DomainError
from app.config import Settings
from app.security.configuration import configuration_lock,atomic_write,write_environment,reject_environment_conflicts
from app.model_gateway.endpoints import validate_endpoint
from app.domain.identifiers import content_hash
from app.model_gateway.profiles import profiles_for, factory_profiles, IDS


def public_provider(provider, secrets):
    return {**{k:v for k,v in provider.items() if k!='api_key_env'},
            'key_configured':bool(secrets.get(provider['api_key_env'])) and not provider.get('key_disabled',False)}

def view(settings):
    with configuration_lock(settings.configuration_root):
        current=Settings(root=settings.root,data_dir=settings.data_dir)
        settings.models,settings.secrets=current.models,current.secrets
        return {'config_version':current.models['config_version'],
                'providers':{role:public_provider(p,current.secrets) for role,p in current.models['providers'].items()},
                'profiles':{ident:public_provider(p,current.secrets) for ident,p in profiles_for(current.models).items()},
                'defaults':{ident:public_provider(p,{}) for ident,p in factory_profiles().items()},
                'active_generation_profile_id':current.models.get('active_generation_profile_id')}

def save(settings,body):
    with configuration_lock(settings.configuration_root):
        current=Settings(root=settings.root,data_dir=settings.data_dir)
        if set(body)-{'expected_version','providers','profiles','active_generation_profile_id'}:
            raise DomainError('MODEL_CONFIG_INVALID','不支持的模型设置字段',422)
        if body.get('expected_version')!=current.models['config_version']:raise DomainError('VERSION_CONFLICT','模型设置版本已变化',409)
        if 'profiles' in body and 'providers' in body:
            raise DomainError('MODEL_CONFIG_INVALID','请分别保存供应商档案或旧主备配置',422)
        is_profiles='profiles' in body
        updates_by_role=body.get('profiles',body.get('providers',{}))
        if not isinstance(updates_by_role,dict):raise DomainError('MODEL_CONFIG_INVALID','模型配置格式不正确',422)
        allowed={'base_url','model_id','timeout','max_output_tokens','context_limit','structured_output_mode','structured_thinking','key'}
        data=json.loads(json.dumps(current.models));env=dict(dotenv_values(settings.configuration_root/'.env.local'))
        profiles=profiles_for(data)
        if 'active_generation_profile_id' in body:
            if body['active_generation_profile_id'] not in IDS:
                raise DomainError('MODEL_CONFIG_INVALID','请选择有效生成供应商',422)
            data['active_generation_profile_id']=body['active_generation_profile_id']
        targets=profiles if is_profiles else data['providers']
        for role,updates in updates_by_role.items():
            if role not in targets or not isinstance(updates,dict) or not set(updates).issubset(allowed):raise DomainError('MODEL_CONFIG_INVALID','不支持的模型配置字段',422)
            provider=targets[role]
            for field in ['timeout','max_output_tokens','context_limit']:
                if field in updates and (type(updates[field]) is not int or not (1 if field=='timeout' else 256)<=updates[field]<=(600 if field=='timeout' else 2000000)):
                    raise DomainError('MODEL_CONFIG_INVALID','超时或 Token 预算范围无效',422)
            if 'model_id' in updates and (not isinstance(updates['model_id'],str) or len(updates['model_id'])>256):
                raise DomainError('MODEL_CONFIG_INVALID','模型 ID 格式无效',422)
            if 'structured_output_mode' in updates and updates['structured_output_mode'] not in {'json_object','none'}:
                raise DomainError('MODEL_CONFIG_INVALID','结构化输出模式无效',422)
            legacy='DEEPSEEK' if provider['api_key_env']=='DEEPSEEK_API_KEY' else 'ARK' if provider['api_key_env']=='ARK_API_KEY' else None
            prefix=provider.get('config_env_prefix') or legacy or 'LAURA_'+role.upper()
            requested={};forbidden=[]
            if 'base_url' in updates:
                if updates['base_url'] or role!='internal':validate_endpoint(updates['base_url'])
                if urlparse(updates['base_url']).netloc!=urlparse(provider['base_url']).netloc:
                    forbidden.append(provider['api_key_env']);provider['key_disabled']=True
                    env.pop(prefix+'_BASE_URL',None);env.pop(prefix+'_MODEL',None)
                    provider['config_id']=role+'-'+content_hash(updates['base_url'])[:12]
                    prefix='LAURA_MODEL_'+provider['config_id'].replace('-','_').upper()
                    provider['config_env_prefix']=prefix
                    provider['api_key_env']=prefix+'_API_KEY'
                requested[prefix+'_BASE_URL']=updates['base_url']
            if 'model_id' in updates:requested[prefix+'_MODEL']=updates['model_id']
            if updates.get('key'):requested[provider['api_key_env']]=updates['key']
            reject_environment_conflicts(requested,forbidden)
            for key,value in updates.items():
                if key=='structured_thinking' and (provider['provider'] not in {'deepseek','glm'} or value not in {'auto','enabled','disabled'}):raise DomainError('MODEL_CONFIG_INVALID','思考模式必须为auto、enabled或disabled且受供应商支持',422)
                if key=='key':
                    if value:
                        if not isinstance(value,str) or len(value)>4096:raise DomainError('MODEL_CONFIG_INVALID','密钥格式无效',422)
                        env[provider['api_key_env']]=value;provider['key_disabled']=False
                else:provider[key]=value
            env[prefix+'_BASE_URL']=provider['base_url'];env[prefix+'_MODEL']=provider['model_id']
        if is_profiles or 'active_generation_profile_id' in body:
            data['generation_profiles']={ident:profiles[ident] for ident in ['glm','internal']}
            selected=profiles[data.get('active_generation_profile_id') or 'glm']
            if data.get('active_generation_profile_id') and (not selected['base_url'] or not selected['model_id']):
                raise DomainError('MODEL_CONFIG_MISSING','请先填写所选供应商的 API 地址和模型 ID',422)
        data['config_version']=str(int(data['config_version'])+1)
        if env!=dict(dotenv_values(settings.configuration_root/'.env.local')):
            write_environment(settings.configuration_root,env)
        atomic_write(settings.configuration_root/'config/models.yaml',yaml.safe_dump(data,allow_unicode=True,sort_keys=False))
        return view(settings)
