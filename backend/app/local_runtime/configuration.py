import json
from pathlib import Path
from app.domain.errors import DomainError
from app.domain.identifiers import content_hash
from app.local_runtime.manager import LocalModelManager
from app.security.configuration import configuration_lock, atomic_write


class LocalRuntimeSettings:
    def __init__(self, settings):
        self.settings = settings
        self.path = settings.data_dir / 'settings' / 'local-model.json'

    def view(self):
        if self.path.exists():
            return json.loads(self.path.read_text('utf-8'))
        root = self.settings.paths.model_root
        manifest = self.settings.local_models.get('active_manifest')
        return {'version': 0, 'model_root': str(root), 'manifest_path': str(root / manifest) if manifest else ''}

    def save(self, body):
        if set(body) != {'expected_version', 'model_root', 'manifest_path'} or type(body.get('expected_version')) is not int:
            raise DomainError('LOCAL_MODEL_CONFIG_INVALID', '请指定模型目录、清单文件和当前版本', 422)
        for key in ['model_root', 'manifest_path']:
            if not isinstance(body[key], str) or not Path(body[key]).is_absolute() or body[key].startswith(('\\\\', '//')):
                raise DomainError('LOCAL_MODEL_PATH_INVALID', '请选择本机绝对路径，不支持网络共享目录', 422)
        with configuration_lock(self.settings.data_dir / 'settings'):
            current = self.view()
            self.assert_version(body['expected_version'])
            result = {'version': current['version'] + 1, 'model_root': str(Path(body['model_root']).resolve()),
                      'manifest_path': str(Path(body['manifest_path']).resolve())}
            atomic_write(self.path, json.dumps(result, ensure_ascii=False))
            return result

    def assert_version(self, expected):
        if type(expected) is not int or expected != self.view()['version']:
            raise DomainError('VERSION_CONFLICT', '模型目录配置已变化，请刷新', 409)

    def manager(self):
        return LocalModelManager(self.settings.data_dir, self.view()['model_root'])

    def frozen_provider(self):
        current = self.view()
        path = Path(current['manifest_path'])
        identity = None
        model_id = 'Qwen3-4B-Instruct-2507'
        if current['manifest_path'] and path.is_file():
            manifest=json.loads(path.read_text('utf-8'))
            identity = content_hash(manifest)
            model_id=manifest.get('model_id',model_id)
        state = self.manager().status()
        return {'manifest_path': current['manifest_path'], 'model_root': current['model_root'],
                'artifact_identity': identity, 'model_id':model_id, 'base_url': f'http://127.0.0.1:{state.get("port",8766)}/v1'}
