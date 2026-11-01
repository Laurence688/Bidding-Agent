from pathlib import Path
import os
import yaml
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]


class Settings:
    def __init__(self, data_dir=None, root=None):
        self.root = Path(root or ROOT)
        from app.local_runtime.paths import RuntimePaths
        self.packaged = os.getenv('SOC_PACKAGED') == '1' or (self.root / 'laura-package.json').is_file()
        self.paths = RuntimePaths.resolve(self.root, self.packaged, data_root=data_dir)
        self.data_dir = self.paths.data_root
        self.data_dir.mkdir(parents=True, exist_ok=True)
        from app.security.configuration import configuration_lock
        with configuration_lock(self.configuration_root):
            if self.packaged:
                import shutil
                self.paths.config_root.mkdir(parents=True, exist_ok=True)
                for source in (self.root / 'config').glob('*.yaml'):
                    target = self.paths.config_root / source.name
                    if not target.exists():
                        shutil.copy2(source, target)
            self._load_configuration()

    @property
    def configuration_root(self):
        return self.paths.user_root if self.packaged else self.root

    def _load_configuration(self):
        # Initialize all user-owned configuration from credential-free templates.
        from app.security.configuration import atomic_write
        for template in (self.root / 'config').glob('*.example.yaml'):
            target = self.configuration_root / 'config' / template.name.replace('.example.yaml', '.yaml')
            if not target.exists():
                atomic_write(target, template.read_text('utf-8'))
        self.products = self.read_yaml('products.yaml')['products']
        self.local = self.read_yaml('local.yaml')
        self.models = self.read_yaml('models.yaml')
        self.processing = self.read_yaml('processing.yaml') if (self.configuration_root / 'config' / 'processing.yaml').exists() else {}
        self.models['node_policies'] = self.processing.get('node_policies', {})
        # 环境值仅读取服务器所需的明确键，不把环境字典放入运行快照。
        self.secrets = {**dotenv_values(self.configuration_root / '.env.local')}
        self.langsmith = self.read_yaml('langsmith.yaml') if (self.configuration_root / 'config' / 'langsmith.yaml').exists() else {'enabled': False}
        self.local_models = self.read_yaml('local_models.yaml') if (self.configuration_root / 'config' / 'local_models.yaml').exists() else {}
        from app.rag.resource_pool import configure_pools
        configure_pools(self.data_dir, self.local_models)
        if self.packaged:
            embedding = Path(self.local['embedding_path'])
            if not embedding.is_absolute():
                self.local['embedding_path'] = str(self.paths.model_root / embedding)
            self.local['knowledge_root'] = str(self.data_dir / 'knowledge' / 'originals')
        for key in ['SOC_LANGSMITH_ENABLED', 'LANGSMITH_API_KEY', 'LANGSMITH_WORKSPACE_ID']:
            if os.getenv(key):
                self.secrets[key] = os.environ[key]
        self.langsmith['enabled'] = str(self.secrets.get('SOC_LANGSMITH_ENABLED', self.langsmith.get('enabled', False))).lower() == 'true'
        self.langsmith['workspace_id'] = self.secrets.get('LANGSMITH_WORKSPACE_ID') or None
        for key in ['DEEPSEEK_API_KEY', 'ARK_API_KEY', 'DEEPSEEK_BASE_URL', 'DEEPSEEK_MODEL', 'ARK_BASE_URL', 'ARK_MODEL']:
            if os.getenv(key):
                self.secrets[key] = os.environ[key]
        providers = list(self.models['providers'].values()) + list(self.models.get('generation_profiles', {}).values())
        for provider in providers:
            key_env=provider['api_key_env']
            if os.getenv(key_env):
                self.secrets[key_env]=os.environ[key_env]
            prefix=provider.get('config_env_prefix') or ('DEEPSEEK' if key_env=='DEEPSEEK_API_KEY' else 'ARK' if key_env=='ARK_API_KEY' else None)
            if not prefix:
                continue
            for field, suffix in [('base_url', 'BASE_URL'), ('model_id', 'MODEL')]:
                if os.getenv(f'{prefix}_{suffix}'):
                    self.secrets[f'{prefix}_{suffix}']=os.environ[f'{prefix}_{suffix}']
                if self.secrets.get(f'{prefix}_{suffix}'):
                    provider[field] = self.secrets[f'{prefix}_{suffix}']

    def read_yaml(self, name):
        path = self.configuration_root / 'config' / name
        if not path.exists():
            template = self.root / 'config' / (Path(name).stem + '.example.yaml')
            if template.is_file():
                from app.security.configuration import atomic_write
                atomic_write(path, template.read_text('utf-8'))
        return yaml.safe_load(path.read_text('utf-8'))

    def product_id(self, name):
        for product in self.products:
            if name in [product['id'], *product['aliases']]:
                return product['id']
        raise ValueError('型号未配置或不属于明确别名')
