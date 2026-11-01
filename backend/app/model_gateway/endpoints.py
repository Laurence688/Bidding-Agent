from urllib.parse import urlparse
from app.domain.errors import DomainError

def is_local_endpoint(value):
 parsed=urlparse(value)
 return parsed.scheme in ('http','https') and parsed.hostname in ('localhost','127.0.0.1','::1') and not parsed.username and not parsed.password

def validate_endpoint(value):
 try:
  parsed=urlparse(value);port=parsed.port
 except (ValueError,TypeError):raise DomainError('MODEL_URL_INVALID','服务地址格式无效',422)
 if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or not (parsed.scheme=='https' or is_local_endpoint(value)):
  raise DomainError('MODEL_URL_INVALID','使用HTTPS云端地址或本机HTTP兼容服务，不允许地址中包含凭据',422)
 return value

def check_model_endpoint(settings, role, profile_id=None):
 from datetime import datetime,timezone
 from app.config import Settings
 from app.security.configuration import configuration_lock
 # Freeze a fresh saved configuration; never hold the writer lock over network I/O.
 with configuration_lock(settings.configuration_root):
  current=Settings(root=settings.root,data_dir=settings.data_dir)
 result=_check_model_endpoint(current,role,profile_id)
 return {**result,'profile_id':profile_id,'config_version':current.models['config_version'],'checked_at':datetime.now(timezone.utc).isoformat()}


def _check_model_endpoint(settings, role, profile_id=None):
 import httpx
 if profile_id is not None:
  from app.model_gateway.profiles import profiles_for
  profiles=profiles_for(settings.models)
  if profile_id not in profiles:raise DomainError('MODEL_CONFIG_INVALID','请选择有效生成供应商',422)
  provider=profiles[profile_id]
 else:
  if role not in settings.models['providers']:raise DomainError('MODEL_CONFIG_INVALID','请选择主模型或备用模型',422)
  provider=settings.models['providers'][role]
 base=validate_endpoint(provider['base_url']).rstrip('/')
 key=None if provider.get('key_disabled') else settings.secrets.get(provider['api_key_env'])
 if not key and not is_local_endpoint(base):return {'connected':False,'message':'请先保存云端密钥'}
 from app.security.execution_policy import policy_for,assert_endpoint_allowed
 policy=policy_for(settings)
 policy['allowed_services']['connection_check']=[base]
 try:assert_endpoint_allowed(policy,base,'connection_check')
 except DomainError as exc:return {'connected':False,'message':str(exc),'error_code':exc.code}
 try:
  with httpx.Client(timeout=6,trust_env=False,follow_redirects=False) as client:
   response=client.get(base+'/models',headers={'Authorization':f'Bearer {key}'} if key else {})
   if response.status_code in {404,405}:return {'connected':False,'model_list_supported':False,'message':'此接口未提供模型列表；尚未验证生成能力，不能据此判断模型不可用。'}
   if response.status_code in {401,403}:return {'connected':False,'message':f'鉴权失败（HTTP {response.status_code}），请核对已保存密钥与权限'}
   if response.status_code>=500:return {'connected':False,'message':f'服务异常（HTTP {response.status_code}），请稍后重试'}
   if response.status_code!=200:return {'connected':False,'message':f'模型列表接口返回 HTTP {response.status_code}，请核对地址与权限'}
  models=[str(x.get('id','')) for x in response.json().get('data',[])]
  available=provider['model_id'] in models
  return {'connected':available,'model_available':available,'message':'服务及所选模型可访问' if available else '服务响应成功，但列表中未找到所选模型；不代表生成接口已验收'}
 except httpx.TimeoutException:return {'connected':False,'message':'连接校验超时，请稍后重试'}
 except (httpx.HTTPError,ValueError,TypeError,AttributeError):return {'connected':False,'message':'未能读取模型列表，请核对服务、网络和地址'}
