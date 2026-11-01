"""Independent local service process; records the real PID behind Windows venv launcher."""
import json
import os
import sys
from pathlib import Path
import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.config import Settings

role = sys.argv[1]
if role not in {'api', 'worker'}:
    raise SystemExit('Unknown service')
settings = Settings()
folder = settings.data_dir / 'runtime'
folder.mkdir(parents=True, exist_ok=True)
port = int(os.getenv('SOC_API_PORT', '8765'))
instance_id = os.getenv('SOC_INSTANCE_ID', '')
record = {'pid': os.getpid(), 'started': psutil.Process().create_time(), 'role': role, 'root': str(ROOT), 'port': port, 'instance_id': instance_id}
temporary = folder / f'{role}.json.tmp'
temporary.write_text(json.dumps(record), 'utf-8')
temporary.replace(folder / f'{role}.json')
if role == 'api':
    import uvicorn
    from app.api.main import create_app
    settings.runtime_instance_id = instance_id
    settings.local['allowed_origins'] = list(dict.fromkeys([*settings.local['allowed_origins'], f'http://127.0.0.1:{port}', f'http://localhost:{port}']))
    uvicorn.run(create_app(settings), host='127.0.0.1', port=port, log_level='info')
else:
    from app.persistence.repositories import Repository
    from app.worker.runner import WorkerRunner
    WorkerRunner(Repository(settings.data_dir), settings).run()
