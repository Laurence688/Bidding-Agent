"""Explicit server-only runtime configuration; no automatic cloud requests."""
import json,os,hashlib
from pathlib import Path
import yaml
from dotenv import dotenv_values
from app.domain.errors import DomainError
from app.model_gateway.endpoints import validate_endpoint

def _view(settings):
 from app.config import Settings
 current=Settings(root=settings.root,data_dir=settings.data_dir)
 data={k:current.langsmith.get(k) for k in ('enabled','endpoint','web_url','project','annotation_queue_id')}
 data['revision']=int(current.langsmith.get('revision',0))
 data.update(endpoint=data.get('endpoint') or 'https://api.smith.langchain.com',web_url=data.get('web_url') or 'https://smith.langchain.com',project=data.get('project') or 'soc-local',enabled=bool(data.get('enabled')))
 return {**data,'version':hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest(),'key_configured':bool(current.secrets.get('LANGSMITH_API_KEY'))}

def _save(settings,body):
 current=_view(settings)
 if body.get('expected_version')!=current['version']:raise DomainError('VERSION_CONFLICT','运行配置已更新，请刷新',409)
 if type(body.get('enabled')) is not bool:raise DomainError('CONFIG_INVALID','请选择同步开关',422)
 if set(body)-{'expected_version','enabled','endpoint','web_url','project','annotation_queue_id','key'}:raise DomainError('CONFIG_INVALID','不支持的运行配置字段',422)
 if body.get('key') is not None and (not isinstance(body['key'],str) or len(body['key'])>4096):raise DomainError('CONFIG_INVALID','密钥格式无效',422)
 values={key:body.get(key) for key in ('enabled','endpoint','web_url','project','annotation_queue_id')}
 values['revision']=current['revision']+1
 for key in ('endpoint','web_url'):validate_endpoint(values.get(key) or '')
 if not isinstance(values['project'],str) or not 1<=len(values['project'].strip())<=100:raise DomainError('CONFIG_INVALID','项目名称无效',422)
 requested={'SOC_LANGSMITH_ENABLED':'true' if values['enabled'] else 'false'}
 if body.get('key'):requested['LANGSMITH_API_KEY']=body['key']
 reject_environment_conflicts(requested,['LANGSMITH_API_KEY'] if values['endpoint']!=current['endpoint'] else [])
 env=dict(dotenv_values(settings.configuration_root/'.env.local'))
 if values['endpoint']!=current['endpoint']:env.pop('LANGSMITH_API_KEY',None)
 if body.get('key'):env['LANGSMITH_API_KEY']=str(body['key'])
 if values['enabled'] and not env.get('LANGSMITH_API_KEY'):raise DomainError('CONFIG_INVALID','启用同步前请配置密钥',422)
 env['SOC_LANGSMITH_ENABLED']='true' if values['enabled'] else 'false'
 write_environment(settings.configuration_root,env)
 atomic_write(settings.configuration_root/'config/langsmith.yaml',yaml.safe_dump(values,allow_unicode=True))
 from app.config import Settings
 updated=Settings(root=settings.root,data_dir=settings.data_dir);settings.langsmith,settings.secrets=updated.langsmith,updated.secrets
 return _view(settings)


from app.security.configuration import configuration_lock,atomic_write,write_environment,reject_environment_conflicts

def view(settings):
 with configuration_lock(settings.configuration_root):return _view(settings)

def save(settings,body):
 with configuration_lock(settings.configuration_root):return _save(settings,body)
