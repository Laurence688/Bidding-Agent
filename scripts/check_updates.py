"""User-invoked portable updater; no Settings initialization before backups."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

PROGRAM=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROGRAM));sys.path.insert(0,str(PROGRAM/'backend'))
from scripts.update_core import UpdateError,_manifest,_program_path,assert_quiescent,local_path,safe_child
from app.services.updates import UpdateService,installed_version
from app.services.update_download import version_tuple


def stage_local_release(source,target):
    source,target=local_path(source,exists=True),local_path(target,exists=True)
    new,files=_manifest(source,verify=True)
    old,_=_manifest(target)
    if version_tuple(new['version'])<=version_tuple(old['version']):return None
    token=uuid.uuid4().hex;work=safe_child(target,'.laura-updates/'+token)
    staged=work/'extract/Laura';staged.mkdir(parents=True)
    for name in [*files,'package-manifest.json']:
        src=safe_child(source,name);dst=safe_child(staged,name);dst.parent.mkdir(parents=True,exist_ok=True)
        try:os.link(src,dst)
        except OSError:shutil.copy2(src,dst)
    return token,staged,new


def create_request(target,token,source,manifest,data_root,restart=True):
    import psutil
    work=safe_child(target,'.laura-updates/'+token)
    request={'schema_version':1,'target_root':str(target),'source_root':str(source),'data_root':str(data_root),
             'restart':restart,'wait_processes':[{'pid':os.getpid(),'created':psutil.Process().create_time()}],
             'expected_version':installed_version(target),'target_version':manifest['version'],'expected_source_commit':manifest.get('source_commit')}
    file=work/'request.json';file.write_text(json.dumps(request,ensure_ascii=False),'utf-8')
    return file


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target',type=Path,default=PROGRAM)
    parser.add_argument('--no-restart',action='store_true')
    args=parser.parse_args(argv)
    target=local_path(args.target,exists=True)
    _manifest(target)
    data_root=local_path(os.getenv('SOC_DATA_DIR') or target/'data')
    print('当前版本：'+installed_version(target),flush=True)
    local=stage_local_release(PROGRAM,target) if PROGRAM.resolve()!=target else None
    if local:
        token,source,manifest=local
        print('已找到当前安装包中的新版，将自动备份并升级原目录。',flush=True)
    else:
        service=UpdateService(target,data_root)
        def show(**changes):
            original(**changes)
            if changes.get('phase')!='downloading':print(changes.get('message',''),flush=True)
        original=service._save;service._save=show
        state=service.check(background=False)
        if state['phase']=='up_to_date':print('当前已是最新版本。',flush=True);return 0
        if state['phase']!='ready':raise UpdateError(state.get('error_code','UPDATE_DOWNLOAD_FAILED'),state['message'])
        token=service._state['token'];source=target/'.laura-updates'/token/'extract/Laura'
        manifest=json.loads((source/'package-manifest.json').read_text('utf-8'))
    # The GUI path hands off through the native host; this standalone command is
    # already outside that Job. Do not interrupt an open installation or its tasks.
    assert_quiescent(target,data_root)
    request=create_request(target,token,source,manifest,data_root,not args.no_restart)
    runtime=source/'.runtimes/python/python.exe'
    if not runtime.is_file():raise UpdateError('UPDATE_RUNTIME_MISSING','更新包的运行环境不完整，请重新下载完整便携包。')
    environment={k:v for k,v in os.environ.items() if k not in {'PYTHONHOME','PYTHONPATH'}}
    subprocess.Popen([str(runtime),'-I',str(source/'scripts/apply_update.py'),'--request',str(request)],cwd=source,stdin=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,
        env={**environment,'PYTHONNOUSERSITE':'1','PYTHONDONTWRITEBYTECODE':'1','PYTHONUTF8':'1','SOC_DATA_DIR':str(data_root)})
    print('更新已开始，将自动备份并保留你的知识库、对话、记忆和设置。完成后自动打开Laura。',flush=True)
    return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except (UpdateError,ValueError,OSError) as exc:
        print('更新尚未完成：'+str(exc),file=sys.stderr)
        raise SystemExit(1)
