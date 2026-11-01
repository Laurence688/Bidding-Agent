from hashlib import sha256
from importlib.metadata import distributions
import subprocess
from functools import lru_cache


@lru_cache(maxsize=1)
def dependencies():
    return sorted({d.metadata['Name'] + '==' + d.version for d in distributions() if d.metadata['Name']})


def provenance(root):
    digest = sha256()
    for folder in ('backend/app', 'config', 'prompts', 'scripts', 'skills', 'packaging'):
        for path in sorted((root / folder).rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts and path.suffix in {'.py', '.yaml', '.txt', '.sql', '.md', '.cmd'}:
                digest.update(str(path.relative_to(root)).encode())
                digest.update(path.read_bytes())
    try:
        commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root, capture_output=True, timeout=5).stdout.decode().strip() or None
        diff = subprocess.run(['git', 'diff', '--binary', 'HEAD', '--', 'backend', 'config', 'prompts'], cwd=root, capture_output=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        commit, diff = None, b''
    return {'git_commit': commit, 'source_fingerprint': digest.hexdigest(), 'dirty_diff_fingerprint': sha256(diff).hexdigest(),
            'dependencies': dependencies()}
