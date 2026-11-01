"""Build a clean Windows runtime ZIP from sanitized Git sources and hashed inputs."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
import zipfile


def safe_relative(name):
    path=PurePosixPath(name)
    if not name or '\\' in name or ':' in name or path.is_absolute() or '..' in path.parts:
        raise ValueError('Unsafe archive member: '+name)
    return path


def validate_source_names(names, approved_content=None):
    if approved_content is None:
        manifest=Path(__file__).resolve().parents[1]/'packaging/public-content.json'
        approved_content=json.loads(manifest.read_text('utf-8')).get('files',{}) if manifest.exists() else {}
    for name in names:
        safe_relative(name)
        parts=PurePosixPath(name).parts
        if (parts[0] in {'data','outputs','docs','tests','.venv','.runtimes','.cache','.git'}
                or 'node_modules' in parts or name.startswith('frontend/src/tests/')
                or (name.startswith('frontend/public/media/') and name not in approved_content)
                or (parts[0].startswith('.env') and name!='.env.example')
                or (name.startswith('config/') and not name.endswith('.example.yaml'))
                or (name.startswith('doc/') and name not in {'doc/README.md','doc/catalog.json'} and name not in approved_content)
                or (name.startswith('doc/') and any(v in name for v in ('归档','上线修复记录','验收记录','问题记录','计划','工具/')))):
            raise ValueError('Private/development input is not allowed: '+name)


def validate_public_models(models):
    approved={'primary':('https://api.deepseek.com','deepseek-flash'),
              'backup':('https://ark.cn-beijing.volces.com/api/v3','doubao-seed-2-1-pro-260915'),
              'glm':('https://open.bigmodel.cn/api/paas/v4','glm-4.7-flash'),'internal':('','')}
    for ident,provider in {**models['providers'],**models.get('generation_profiles',{})}.items():
        if ident not in approved or (provider.get('base_url',''),provider.get('model_id','')) != approved[ident]:
            raise ValueError('Only reviewed public presets and an empty internal profile may be published')
        if any(provider.get(k) for k in ('key','api_key','token','secret')):
            raise ValueError('Provider credentials are forbidden in public templates')


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def verify_hash(path, expected):
    if digest(path)!=expected.lower():raise ValueError('Runtime hash mismatch: '+Path(path).name)


def unpack_python(archive, destination):
    with tarfile.open(archive,'r:gz') as package:
        for item in package.getmembers():
            relative=safe_relative(item.name)
            if relative.parts[0]!='python' or item.issym() or item.islnk():
                raise ValueError('Unexpected Python distribution layout')
            relative=Path(*relative.parts[1:])
            if not relative.parts:continue
            if any(p=='__pycache__' for p in relative.parts) or relative.suffix in {'.pyc','.pyo'}:continue
            if relative.parts[0]=='Scripts':continue  # generated entry points may contain build-machine paths
            target=destination/relative
            if item.isdir():target.mkdir(parents=True,exist_ok=True)
            elif item.isfile():
                target.parent.mkdir(parents=True,exist_ok=True)
                with package.extractfile(item) as src,target.open('wb') as dst:shutil.copyfileobj(src,dst)
            else:raise ValueError('Unsupported Python archive member')


def unpack_node(archive,destination):
    with zipfile.ZipFile(archive) as package:
        for item in package.infolist():
            relative=safe_relative(item.filename)
            if relative.parts[0]!='node-v24.18.0-win-x64':raise ValueError('Unexpected Node distribution layout')
            relative=Path(*relative.parts[1:])
            if not relative.parts:continue
            target=destination/relative
            if item.is_dir():target.mkdir(parents=True,exist_ok=True)
            else:
                target.parent.mkdir(parents=True,exist_ok=True)
                with package.open(item) as src,target.open('wb') as dst:shutil.copyfileobj(src,dst)


def prepare(args):
    from packaging.utils import canonicalize_name,parse_wheel_filename
    import yaml
    source,stage=args.source.resolve(),args.stage.resolve()
    if stage.exists() or args.output.exists():raise FileExistsError('Use a new stage and output file')
    verify_hash(args.python_archive,args.python_sha256)
    verify_hash(args.node_archive,args.node_sha256)
    if subprocess.check_output(['git','-C',str(source),'status','--porcelain','--untracked-files=all']).strip():
        raise ValueError('Commit the reviewed sanitized source before packaging')
    names=list(filter(None,subprocess.check_output(['git','-C',str(source),'ls-files','-z']).decode().split('\0')))
    content_manifest=json.loads((source/'packaging/public-content.json').read_text('utf-8'))
    validate_source_names(names,content_manifest['files'])
    for name,expected in content_manifest['files'].items():
        if name not in names:raise ValueError('Approved content is missing: '+name)
        verify_hash(source/name,expected)
    models=yaml.safe_load((source/'config/models.example.yaml').read_text('utf-8'))
    validate_public_models(models)
    if yaml.safe_load((source/'config/products.example.yaml').read_text('utf-8'))!={'products':[]}:
        raise ValueError('Publish an empty product catalog')
    stage.mkdir(parents=True)
    for name in names:
        original=source/name
        if original.is_symlink():raise ValueError('Source symlink not allowed')
        target=stage/name;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(original,target)
    python=stage/'.runtimes/python';node=stage/'.runtimes/node'
    print('Extracting verified Python and Node runtimes',flush=True)
    unpack_python(args.python_archive,python)
    unpack_node(args.node_archive,node)
    wheels={}
    for file in args.wheelhouse.glob('*.whl'):
        name,version,*_=parse_wheel_filename(file.name)
        wheels.setdefault((canonicalize_name(name),str(version)),[]).append(file)
    requirements=[]
    for line in (source/'backend/requirements.lock.txt').read_text('utf-8').splitlines():
        if not line.strip() or line.startswith('#'):continue
        name,version=line.split('==')
        matches=wheels.get((canonicalize_name(name),version),[])
        if not matches:raise ValueError('Missing pinned wheel: '+line)
        requirements.append(line+''.join(' --hash=sha256:'+digest(p) for p in matches))
    hashed_lock=stage.parent/(stage.name+'-hashed-requirements.txt')
    hashed_lock.write_text('\n'.join(requirements)+'\n','utf-8')
    env={k:v for k,v in os.environ.items() if not k.startswith('PYTHON')}
    env.update(PYTHONNOUSERSITE='1',PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1')
    temporary=stage.parent/(stage.name+'-build-tmp')
    temporary.mkdir(exist_ok=True)
    env.update(TMP=str(temporary),TEMP=str(temporary))
    env.pop('NODE_OPTIONS',None)
    for key in list(env):
        if key.startswith('VITE_'):env.pop(key)
    env['PATH']=str(node)+os.pathsep+env.get('PATH','')
    for folder in (source/'frontend/src',source/'frontend/public'):
        for file in folder.rglob('*'):
            if file.is_file() and file.relative_to(source).as_posix() not in names:
                raise ValueError('Untracked frontend build input: '+file.name)
    if any((source/'frontend'/name).exists() for name in ('.env','.env.local','.env.production','.env.production.local')):
        raise ValueError('Do not build public frontend with local environment files')
    print('Building frontend using bundled Node',flush=True)
    subprocess.run([str(node/'node.exe'),str(node/'node_modules/npm/bin/npm-cli.js'),'--prefix',str(source/'frontend'),'run','build'],check=True,env=env,cwd=source)
    shutil.copytree(source/'frontend/dist',stage/'frontend/dist')
    executable=str(python/'python.exe')
    print('Installing pinned backend wheels offline into clean Python',flush=True)
    subprocess.run([executable,'-m','pip','--isolated','--disable-pip-version-check','install','--quiet','--no-index','--no-deps','--no-compile','--require-hashes','--find-links',str(args.wheelhouse.resolve()),'-r',str(hashed_lock),'--target',str(python/'Lib/site-packages'),'--upgrade'],check=True,env=env,cwd=stage)
    subprocess.run([executable,'-m','pip','check'],check=True,env=env,cwd=stage)
    for folder in [python/'Lib/site-packages/Scripts',python/'Lib/site-packages/bin',python/'Scripts']:
        if folder.exists():
            assert folder.resolve().is_relative_to(stage)
            shutil.rmtree(folder)
    for file in python.rglob('*'):
        if file.is_file() and file.suffix in {'.pyc','.pyo'}:file.unlink()
    (stage/'licenses').mkdir(exist_ok=True)
    shutil.copyfile(python/'LICENSE.txt',stage/'licenses/Python-LICENSE.txt')
    shutil.copyfile(node/'LICENSE',stage/'licenses/Node-LICENSE.txt')
    config={'schema_version':1,'python':'3.11.17','node':'24.18.0','backend_preinstalled':True,'frontend_prebuilt':True}
    (stage/'laura-bundle.json').write_text(json.dumps(config,indent=2)+'\n','utf-8')
    manifest={'schema_version':1,'version':args.version,'source_commit':subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD']).decode().strip(),'runtimes':{'python_archive_sha256':args.python_sha256,'node_archive_sha256':args.node_sha256},'files':[]}
    for file in sorted(stage.rglob('*')):
        if file.is_file():manifest['files'].append({'path':file.relative_to(stage).as_posix(),'size':file.stat().st_size,'sha256':digest(file)})
    (stage/'package-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),'utf-8')
    print('Writing portable ZIP',flush=True)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(args.output,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=4,allowZip64=True) as archive:
        for file in sorted(stage.rglob('*')):
            if file.is_file():archive.write(file,'Laura/'+file.relative_to(stage).as_posix())
    print(json.dumps({'files':len(manifest['files'])+1,'zip_bytes':args.output.stat().st_size,'sha256':digest(args.output)}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version',default='0.3.0-portable')
    for name in ('source','stage','output','python-archive','node-archive','wheelhouse'):
        parser.add_argument('--'+name,type=Path,required=True)
    for name in ('python-sha256','node-sha256'):parser.add_argument('--'+name,required=True)
    prepare(parser.parse_args())
