"""Offline post-install dependency/configuration/schema probe; never calls a model."""
import argparse
import importlib
import os
from pathlib import Path
import sys


def probe(root, data_root=None):
    root = Path(root).resolve()
    sys.path.insert(0, str(root / 'backend'))
    sys.path.insert(0, str(root))
    for name in ('fastapi', 'uvicorn', 'yaml', 'dotenv', 'psutil', 'httpx', 'openpyxl', 'pydantic', 'langgraph', 'numpy', 'faiss'):
        importlib.import_module(name)
    if not (root / 'frontend/dist/index.html').is_file():
        raise RuntimeError('Frontend build is missing')
    os.environ.pop('SOC_PACKAGED', None)
    os.environ['SOC_DATA_DIR'] = str(Path(data_root or root / 'data').resolve())
    from app.config import Settings
    from app.persistence.repositories import Repository
    settings = Settings(root=root, data_dir=os.environ['SOC_DATA_DIR'])
    repository = Repository(settings.data_dir)
    with repository.connection() as conn:
        if conn.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise RuntimeError('Database integrity check failed')
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--data-root', type=Path)
    args = parser.parse_args(argv)
    try:
        probe(args.root, args.data_root)
    except Exception:
        # Do not include configuration/secret values in diagnostics.
        print('Laura update dependency/configuration/schema probe failed.', file=sys.stderr)
        return 1
    print('Laura update probe passed.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
