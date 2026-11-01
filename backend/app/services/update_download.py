"""Explicit, credential-free downloads from the project's GitHub releases."""
import hashlib
from pathlib import Path
import re
import stat
from urllib.parse import urljoin, urlsplit

# Repository ID survives the verified Bidding-Agent -> Bid-Agent rename.
RELEASES_URL='https://api.github.com/repositories/1440902300/releases?per_page=30'
ASSET_NAME='Laura-Portable-Windows-x64.zip'
MAX_ARCHIVE_BYTES=2*1024**3


class UpdateDownloadError(Exception):
    def __init__(self,code,message):
        self.code,self.message=code,message
        super().__init__(message)


def safe_update_path(value, *, mutable=False):
    """Check every existing component before reading or writing update caches."""
    path=Path(value)
    if not path.is_absolute() or str(path).startswith(('\\\\','//')):
        raise UpdateDownloadError('UPDATE_PATH_UNSAFE','更新缓存必须使用本机绝对路径')
    for component in [*reversed(path.parents),path]:
        try:info=component.lstat()
        except FileNotFoundError:continue
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
            raise UpdateDownloadError('UPDATE_PATH_UNSAFE','更新缓存不能经过符号链接或目录联接，已停止写入')
        if mutable and component==path and stat.S_ISREG(info.st_mode) and info.st_nlink>1:
            raise UpdateDownloadError('UPDATE_PATH_UNSAFE','更新临时下载文件不能使用文件链接，已停止写入')
    return path.resolve()


def version_tuple(value):
    match=re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)(?:-portable|-dev)?',str(value))
    if not match:raise UpdateDownloadError('UPDATE_VERSION_INVALID','更新版本格式无效')
    return tuple(map(int,match.groups()))


def _download_url(value):
    parsed=urlsplit(value)
    if parsed.scheme!='https' or parsed.username or parsed.password or parsed.port not in (None,443):
        raise UpdateDownloadError('UPDATE_URL_DENIED','更新下载地址未通过校验')
    if parsed.hostname=='github.com':
        valid=any(parsed.path.startswith('/Laurence688/'+name+'/releases/download/') for name in ('Bid-Agent','Bidding-Agent'))
    else:valid=parsed.hostname in {'release-assets.githubusercontent.com','objects.githubusercontent.com'}
    if not valid:raise UpdateDownloadError('UPDATE_URL_DENIED','更新下载地址未通过校验')
    return value


def discover_latest(client,current_version):
    response=client.get(RELEASES_URL,headers={'Accept':'application/vnd.github+json','User-Agent':'Laura-Updater'},follow_redirects=False)
    if response.status_code==403 or response.status_code==429:
        raise UpdateDownloadError('UPDATE_RATE_LIMITED','更新服务暂时限制请求，请稍后重试')
    if response.status_code!=200:raise UpdateDownloadError('UPDATE_CHECK_FAILED','暂时无法访问更新服务，请检查网络后重试')
    rows=response.json()
    if not isinstance(rows,list):raise UpdateDownloadError('UPDATE_CHECK_FAILED','更新服务返回格式无效')
    candidates=[]
    for row in rows:
        if not isinstance(row,dict) or row.get('draft'):continue
        try:version=version_tuple(row.get('tag_name',''))
        except UpdateDownloadError:continue
        for asset in row.get('assets',[]):
            digest=asset.get('digest') or ''
            if asset.get('name')!=ASSET_NAME or not re.fullmatch(r'sha256:[a-fA-F0-9]{64}',digest):continue
            size=asset.get('size')
            if type(size) is not int or not 0<size<=MAX_ARCHIVE_BYTES:continue
            try:url=_download_url(asset.get('browser_download_url',''))
            except (UpdateDownloadError,ValueError):continue
            if urlsplit(url).hostname!='github.com':continue
            candidates.append((version,{'version':row['tag_name'].removeprefix('v'),'url':url,'size':size,'sha256':digest[7:].lower(),'notes':str(row.get('body') or '')[:12000],'source_commit':row.get('target_commitish') if re.fullmatch(r'[a-fA-F0-9]{40}',str(row.get('target_commitish',''))) else None}))
    if not candidates:raise UpdateDownloadError('UPDATE_NOT_AVAILABLE','暂未找到可验证的 Windows 便携更新，请稍后重试')
    candidate=max(candidates,key=lambda item:item[0])[1]
    candidate['available']=version_tuple(candidate['version'])>version_tuple(current_version)
    return candidate


def _sha(path):
    with safe_update_path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def download_release(client,release,destination,*,cancel=None,progress=None):
    destination=safe_update_path(destination)
    partial=safe_update_path(destination.with_suffix(destination.suffix+'.part'),mutable=True)
    safe_update_path(destination.parent).mkdir(parents=True,exist_ok=True)
    if destination.exists() and destination.stat().st_size==release['size'] and _sha(destination)==release['sha256']:return destination
    start=partial.stat().st_size if partial.exists() else 0
    if start==release['size'] and _sha(partial)==release['sha256']:
        safe_update_path(partial,mutable=True).replace(safe_update_path(destination));return destination
    if start>=release['size']:
        safe_update_path(partial,mutable=True).unlink();start=0
    url=_download_url(release['url'])
    for redirect in range(6):
        if cancel is not None and cancel.is_set():raise UpdateDownloadError('UPDATE_CANCELLED','下载已暂停，下次检查更新时继续')
        headers={'User-Agent':'Laura-Updater'}
        if start:headers['Range']=f'bytes={start}-'
        with client.stream('GET',url,headers=headers,follow_redirects=False) as response:
            if response.status_code in {301,302,303,307,308}:
                url=_download_url(urljoin(url,response.headers.get('Location','')));continue
            if response.status_code not in {200,206}:raise UpdateDownloadError('UPDATE_DOWNLOAD_FAILED','新版下载失败，可稍后检查更新继续下载')
            if response.status_code==206:
                match=re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)',response.headers.get('Content-Range',''))
                if not match or int(match[1])!=start or int(match[3])!=release['size']:
                    raise UpdateDownloadError('UPDATE_DOWNLOAD_FAILED','下载续传范围无效，请重试')
            else:start=0
            size=start
            with safe_update_path(partial,mutable=True).open('ab' if start else 'wb') as output:
                for chunk in response.iter_bytes(256*1024):
                    if cancel is not None and cancel.is_set():raise UpdateDownloadError('UPDATE_CANCELLED','下载已暂停，下次检查更新时继续')
                    size+=len(chunk)
                    if size>release['size']:raise UpdateDownloadError('UPDATE_DOWNLOAD_INVALID','下载文件超出预期大小')
                    output.write(chunk)
                    if progress:progress({'phase':'downloading','completed':size,'total':release['size'],'message':'正在下载新版'})
            if size!=release['size'] or _sha(partial)!=release['sha256']:
                safe_update_path(partial,mutable=True).unlink(missing_ok=True)
                raise UpdateDownloadError('UPDATE_HASH_MISMATCH','更新文件校验未通过，已阻止安装，请重新检查更新')
            safe_update_path(partial,mutable=True).replace(safe_update_path(destination))
            return destination
    raise UpdateDownloadError('UPDATE_REDIRECT_LIMIT','更新下载重定向过多，已停止')
