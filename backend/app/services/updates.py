"""Release checking and staging; installation is handed to the native host."""
from contextlib import closing
import importlib.util
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import sys
import uuid
import httpx
from app.security.configuration import atomic_write
from app.services.update_download import UpdateDownloadError,discover_latest,download_release,safe_update_path,version_tuple

APP_VERSION='0.4.0'


class UpdateServiceError(Exception):
    def __init__(self,code,message):self.code,self.message=code,message;super().__init__(message)


def load_update_core():
    path=Path(__file__).resolve().parents[3]/'scripts/update_core.py'
    spec=importlib.util.spec_from_file_location('laura_update_core',path)
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    return module


def installed_version(root):
    try:return json.loads((Path(root)/'package-manifest.json').read_text('utf-8'))['version']
    except (OSError,ValueError,KeyError):return APP_VERSION+'-dev'


class UpdateService:
    def __init__(self,root,data_dir,*,client_factory=None,extractor=None):
        self.root,self.data_dir=Path(root).resolve(),Path(data_dir).resolve()
        self.cache=self.root/'.laura-updates';self.path=self.cache/'state.json'
        self.supported=(self.root/'laura-bundle.json').is_file() and not (self.root/'.git').exists()
        self.client_factory=client_factory or (lambda:httpx.Client(timeout=httpx.Timeout(60,connect=10),trust_env=False,follow_redirects=False))
        self.extractor=extractor
        self.lock=threading.RLock();self.cancelled=threading.Event();self.thread=None
        try:
            self._state=json.loads(self._cache_file('state.json').read_text('utf-8'))
            if not isinstance(self._state,dict):raise ValueError('invalid update state')
        except UpdateDownloadError as exc:self._state={'phase':'failed','message':exc.message,'error_code':exc.code}
        except (OSError,ValueError):self._state={'phase':'idle','message':'检查后自动下载可验证的新版'}
        if self._state.get('phase') in {'checking','downloading','verifying'}:
            self._state.update(phase='interrupted',message='上次下载未完成，检查更新可继续')

    def _cache_file(self,name):
        path=safe_update_path(self.cache/name)
        if not path.is_relative_to(self.root):
            raise UpdateDownloadError('UPDATE_PATH_UNSAFE','更新缓存越过了安装目录，已停止写入')
        return path

    def _save(self,**changes):
        with self.lock:
            self._state.update(changes)
            try:atomic_write(self._cache_file('state.json'),json.dumps(self._state,ensure_ascii=False))
            except UpdateDownloadError as exc:
                self._state.update(phase='failed',message=exc.message,error_code=exc.code)
                raise UpdateServiceError(exc.code,exc.message) from exc

    def _reconcile_helper(self):
        with self.lock:
            token=self._state.get('token','')
            if self._state.get('phase')!='installing' or not isinstance(token,str) or not re.fullmatch('[a-f0-9]{32}',token):return
            try:result=json.loads(self._cache_file(token+'/state.json').read_text('utf-8'))
            except UpdateDownloadError as exc:
                self._state.update(phase='failed',message=exc.message,error_code=exc.code);return
            except (OSError,ValueError):return
            if not isinstance(result,dict) or result.get('status') not in {'completed','failed'}:return
            outcome={key:result[key] for key in ('installed','rolled_back','original_preserved','restarted','restart_required','recovery_required') if key in result}
            try:self._save(phase=result['status'],message=result.get('message') or ('更新完成' if result['status']=='completed' else '更新未完成，已保留备份'),error_code=result.get('code'),**outcome)
            except (OSError,UpdateServiceError):pass

    def status(self):
        self._reconcile_helper()
        with self.lock:
            state=dict(self._state)
        keys={'phase','message','completed','total','latest_version','notes','error_code','installed','rolled_back','original_preserved','restarted','restart_required','recovery_required'}
        result={k:v for k,v in state.items() if k in keys}
        result.update(installed_version=installed_version(self.root),supported=self.supported,can_install=self.supported and state.get('phase')=='ready' and self.launcher_available())
        return result

    def launcher_available(self):
        from app.services.browser_sessions import current_launcher_token
        return bool(current_launcher_token(self.data_dir))

    def check(self,*,background=True):
        if not self.supported:raise UpdateServiceError('UPDATE_DEVELOPMENT','开发目录不执行自动覆盖；请使用便携版的检查更新入口')
        with self.lock:
            self._reconcile_helper()
            try:self._cache_file('state.json')
            except UpdateDownloadError as exc:
                self._state.update(phase='failed',message=exc.message,error_code=exc.code)
                raise UpdateServiceError(exc.code,exc.message) from exc
            if self.thread and self.thread.is_alive():return self.status()
            if self._state.get('phase')=='installing':raise UpdateServiceError('UPDATE_BUSY','正在安装更新，请等待重启完成')
            for key in ('installed','rolled_back','original_preserved','restarted','restart_required','recovery_required'):
                self._state.pop(key,None)
            self.cancelled.clear();self._save(phase='checking',message='正在检查新版',error_code=None,completed=0,total=0)
            if background:
                self.thread=threading.Thread(target=self._check,name='Laura-release-download',daemon=True);self.thread.start()
            else:self._check()
        return self.status()

    def _check(self):
        try:
            with self.client_factory() as client:
                release=discover_latest(client,installed_version(self.root))
                self._save(latest_version=release['version'],notes=release['notes'])
                if not release['available']:
                    self._save(phase='up_to_date',message='当前已是最新版本');return
                token=uuid.uuid4().hex
                self._save(token=token,release=release,phase='downloading',message='发现新版，正在自动下载')
                archive=self._cache_file('downloads/'+release['version']+'-'+release['sha256'][:12]+'.zip')
                download_release(client,release,archive,cancel=self.cancelled,progress=lambda event:self._save(**event))
            self._save(phase='verifying',message='正在校验并准备更新')
            extract=self.extractor or load_update_core().extract_verified_zip
            source=extract(archive,self._cache_file(token+'/extract'),release['sha256'])
            manifest=json.loads((source/'package-manifest.json').read_text('utf-8'))
            if version_tuple(manifest['version'])!=version_tuple(release['version']):raise UpdateServiceError('UPDATE_VERSION_MISMATCH','更新包版本与发布信息不一致')
            if release.get('source_commit') and manifest.get('source_commit')!=release['source_commit']:raise UpdateServiceError('UPDATE_SOURCE_MISMATCH','更新包来源与发布信息不一致')
            if self.cancelled.is_set():self._save(phase='paused',message='下载已暂停，可稍后继续');return
            self._save(phase='ready',message='新版已就绪，安装时将自动备份并保留用户数据',completed=release['size'],total=release['size'])
        except Exception as exc:
            code=getattr(exc,'code','UPDATE_CHECK_FAILED')
            message=getattr(exc,'message','更新服务暂时不可用，请检查网络后重试')
            try:self._save(phase='paused' if code=='UPDATE_CANCELLED' else 'failed',message=message,error_code=code)
            except (OSError,UpdateServiceError):pass

    def pause(self):
        self.cancelled.set();return self.status()

    def _assert_idle(self):
        database=self.data_dir/'app.db'
        if not database.exists():return
        try:
            with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)) as db:
                row=db.execute("SELECT 1 FROM records WHERE kind IN ('job','kb_build') AND deleted=0 AND json_extract(data,'$.status') IN ('queued','running','cancelling','building') LIMIT 1").fetchone()
            if row:raise UpdateServiceError('UPDATE_BUSY','仍有排队或运行任务，完成后再安装；不会自动取消任务')
        except sqlite3.Error as exc:raise UpdateServiceError('UPDATE_DATA_CHECK_FAILED','无法确认任务状态，已阻止安装') from exc

    def prepare_install(self,confirmed):
        if confirmed is not True:raise UpdateServiceError('UPDATE_CONFIRM_REQUIRED','请确认自动备份、安装并重启')
        if not self.supported:raise UpdateServiceError('UPDATE_DEVELOPMENT','开发目录不能执行便携版覆盖更新')
        self._assert_idle()
        with self.lock:
            if self._state.get('phase')!='ready':raise UpdateServiceError('UPDATE_NOT_READY','请先检查更新并等待下载校验完成')
            token=self._state.get('token','')
            if not re.fullmatch('[a-f0-9]{32}',token):raise UpdateServiceError('UPDATE_NOT_READY','更新请求无效，请重新检查')
            if not self.launcher_available():raise UpdateServiceError('UPDATE_LAUNCHER_REQUIRED','新版已下载，请关闭Laura后双击“检查更新Laura.cmd”完成安装')
            try:source=self._cache_file(token+'/extract/Laura')
            except UpdateDownloadError as exc:raise UpdateServiceError(exc.code,exc.message) from exc
            if not (source/'scripts/apply_update.py').is_file():raise UpdateServiceError('UPDATE_NOT_READY','更新包缺少安装组件，请重新下载')
            release=self._state['release']
            record=json.loads((self.data_dir/'runtime/browser-launcher.json').read_text('utf-8'))
            wait=[{'pid':record['pid'],'created':record['started']}]
            try:
                import psutil
                parent=psutil.Process(record['pid']).parent()
                if parent and Path(parent.exe()).resolve()==(self.root/'Laura.exe').resolve():wait.append({'pid':parent.pid,'created':parent.create_time()})
            except (OSError,psutil.Error):pass
            request={'schema_version':1,'target_root':str(self.root),'source_root':str(source),'data_root':str(self.data_dir),'wait_processes':wait,'restart':True,'expected_version':installed_version(self.root),'target_version':release['version'],'expected_source_commit':release.get('source_commit')}
            try:atomic_write(self._cache_file(token+'/request.json'),json.dumps(request,ensure_ascii=False))
            except UpdateDownloadError as exc:raise UpdateServiceError(exc.code,exc.message) from exc
            self._save(phase='installing',message='即将自动备份并重启，请等待新版打开')
            return self.status()

    def launcher_update_token(self):
        self._reconcile_helper()
        with self.lock:return self._state.get('token') if self._state.get('phase')=='installing' else None
