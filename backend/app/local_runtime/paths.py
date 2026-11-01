"""Program resources are immutable; packaged installs keep state per Windows user."""
from dataclasses import dataclass
from pathlib import Path
import os
import json


@dataclass(frozen=True)
class RuntimePaths:
    program_root: Path
    config_root: Path
    data_root: Path
    model_root: Path
    user_root: Path

    @classmethod
    def resolve(cls, program_root, packaged=False, data_root=None, model_root=None):
        program = Path(program_root).resolve()
        user = Path(os.getenv('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local') / 'Laura' if packaged else program
        choices = {}
        if packaged and (user / 'install.json').is_file():
            choices = json.loads((user / 'install.json').read_text('utf-8'))
            for value in choices.values():
                if value is not None and (not isinstance(value,str) or not Path(value).is_absolute() or value.startswith(('\\\\','//'))):
                    raise ValueError('安装目录配置无效，必须使用本机绝对路径')
        return cls(program, (user / 'config').resolve(),
                   Path(data_root or os.getenv('SOC_DATA_DIR') or choices.get('data_root') or user / 'data').resolve(),
                   Path(model_root or os.getenv('SOC_MODEL_DIR') or choices.get('model_root') or program / 'models').resolve(), user.resolve())
