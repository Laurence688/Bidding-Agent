"""One reentrant thread/process lock for all mutable server configuration."""
from contextlib import contextmanager
from pathlib import Path
import os,threading,time,json,uuid
from app.domain.errors import DomainError

_guard=threading.Lock()
_locks={}
_local=threading.local()

@contextmanager
def configuration_lock(root, timeout_seconds=30):
    root=Path(root).resolve();key=str(root)
    with _guard:lock=_locks.setdefault(key,threading.RLock())
    with lock:
        depths=getattr(_local,'depths',{})
        if depths.get(key,0):
            depths[key]+=1
            try:yield
            finally:depths[key]-=1
            return
        folder=root/'.cache';folder.mkdir(parents=True,exist_ok=True)
        stream=(folder/'configuration.lock').open('a+b');stream.seek(0,os.SEEK_END)
        if stream.tell()==0:stream.write(b'0');stream.flush()
        acquired=False;deadline=time.monotonic()+timeout_seconds
        try:
            while not acquired:
                stream.seek(0)
                try:
                    if os.name=='nt':
                        import msvcrt
                        msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                    acquired=True
                except (OSError,BlockingIOError):
                    if time.monotonic()>deadline:raise DomainError('CONFIG_BUSY','配置正在由其他进程保存，请稍后重试',409)
                    time.sleep(.02)
            depths[key]=1;_local.depths=depths
            yield
        finally:
            depths[key]=0
            if acquired:
                stream.seek(0)
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
            stream.close()

def atomic_write(path,text):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('w',encoding='utf-8',newline='\n') as stream:
            stream.write(text);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)

def write_environment(root,values):
    atomic_write(Path(root)/'.env.local','\n'.join(f'{k}={json.dumps(v or "",ensure_ascii=False)}' for k,v in values.items())+'\n')

def reject_environment_conflicts(requested,forbidden=()):
    conflicts=[name for name,value in requested.items() if os.environ.get(name) and str(value)!=os.environ[name]]
    conflicts.extend(name for name in forbidden if os.environ.get(name))
    if conflicts:raise DomainError('ENV_CONFIG_CONFLICT','服务进程环境变量正在覆盖此配置，请先清理对应环境变量并重启服务后保存',409,{'fields':sorted(set(conflicts))})
