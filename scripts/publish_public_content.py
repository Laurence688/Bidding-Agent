"""Publish explicitly authored public documentation; never copy local archives."""
from pathlib import Path
import hashlib
import json
import shutil

EXTRA_DOCS = {
'03-界面与交互/界面与交互设计.md': '# 界面与交互说明\n\n顶部导航按工作中心、数据管理、效果评估、效率工具、文档中心和系统设置组织。当前项目决定会话、任务和知识范围；会话侧栏用于切换与管理对话，工作区保留必要草稿。\n\n首次创建项目若无产品，应通过可点击入口完成登记，回来后继续创建。表单错误应靠近输入，可关闭并随编辑清除；不能让一次失败长期占据所有页面。\n\n知识库采用“上传 → 切分 → 预览 → 向量化 → 发布”的显式流程。构建后台运行，通知显示进度，关闭通知不取消任务。项目回收站提供可恢复操作，运行任务存在时明确阻拦。\n\n模型设置区分接口草稿、已保存配置和当前生成接口。校验只使用保存值；联网授权单独明确保存，不随着填 Key 自动开启。文档中心提供目录、搜索、正文和来源跳转；公开文档只包含使用与现行设计说明。\n',
'03-界面与交互/知识库来源类型与整篇保留策略设计.md': '# 知识来源类型与整篇保留\n\n来源类型帮助解释证据的用途，不能代替对原文、型号和条件的核对。产品说明、功能资料、参数资料与其他文档应按实际内容选择；“其他”不是免审来源。\n\nWord 与文字型 PDF 可以按规则切分，也可在内容较短且预算允许时整篇作为单片段保留。整篇策略减少上下文被切断的风险，但会增加检索结果与模型上下文长度，应在预览中确认字符量、定位和重复内容。\n\nExcel 使用明确的工作表、表头、行列与指标配置，不把整表任意拼接成没有位置的证据。切分规则改变会产生新版本，必须重新构建并发布；历史任务仍使用当时的知识版本。\n',
'05-质量与验收/运行观测与评测.md': '# 运行观测与评测说明\n\n任务记录用于查看状态、步骤、失败原因、模型调用、用量与耗时。接口连接成功、模型返回成功、流程执行成功、业务结论正确是不同层次，不应互相替代。\n\n质量核对关注要求是否完整、数值与条件是否保留、检索是否命中适用资料、引用是否足以支持结论、四态是否合理和英文口径是否越过证据边界。人工标注和固定样本可用于比较不同配置，但需要记录样本、版本与判定标准。\n\n本地记录默认保存在使用者本机。LangSmith 等外部同步使用独立设置，只有明确启用才进行相应同步；生成 API 联网许可不会自动开启观测同步。\n\n本公开文档说明机制，不包含作者的历史测试、日志、验收结果或业务数据，也不据工程回归宣称业务准确率、速度或成本收益。\n',
}


def publish_branding(source, destination):
    source,destination=Path(source),Path(destination)
    name='frontend/public/laura-brand.png'
    target=destination/name;target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(source/name,target)
    for name in ('frontend/src/App.tsx','frontend/src/pages/Workspace.tsx','frontend/src/pages/Knowledge.tsx','frontend/index.html'):
        path=destination/name
        if path.is_file():
            text=path.read_text('utf-8').replace('/laura-mark.svg','/laura-brand.png')
            if name.endswith('index.html'):text=text.replace('type="image/svg+xml"','type="image/png"')
            path.write_text(text,'utf-8',newline='\n')
    return {'frontend/public/laura-brand.png':hashlib.sha256(target.read_bytes()).hexdigest()}


def publish(source, destination, guide_assets=None):
    import re
    from public_documentation import source_documents, VERSION
    source,destination=Path(source),Path(destination)
    guide_assets=Path(guide_assets) if guide_assets is not None else source/'.cache/public-guides-v04'
    if not (guide_assets/'manifest.json').is_file():
        raise ValueError('缺少新版合成截图登记文件，请先捕获 v0.4.0 使用指南截图')
    capture=json.loads((guide_assets/'manifest.json').read_text('utf-8'))
    if capture.get('publication_version')!=VERSION:
        raise ValueError('截图版本不匹配，禁止回退复制旧截图')
    folder=destination/'doc';folder.mkdir(parents=True,exist_ok=True)
    catalog=json.loads((source/'doc/catalog.json').read_text('utf-8'))
    metadata={item['path']:item for item in catalog}
    extras=dict(EXTRA_DOCS)
    documents={**source_documents(source,extras),**{name:text.replace('公司 API','内网API') for name,text in extras.items()}}
    documents={'README.md':'# Laura 文档中心\n\n出版版本：'+VERSION+'\n\n完整使用指南与当前产品、路线、架构及技术说明。\n\n'+'\n'.join('- ['+name.split('/')[-1][:-3]+']('+name+')' for name in documents)+'\n\n首页视频和配图为合成演示，不代表真实业务结果或模型质量。\n',**documents}
    approved=publish_branding(source,destination)
    entries=[]
    for index,(name,text) in enumerate(documents.items()):
        target=folder/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(text,'utf-8',newline='\n')
        approved['doc/'+name]=hashlib.sha256(target.read_bytes()).hexdigest()
        entries.append({'path':name,'id':metadata.get(name,{}).get('id','public-'+str(index)), 'title':text.splitlines()[0].lstrip('# '),'scope':'guide' if name.startswith('00-使用指南/') else 'current','order':index,'publication_version':VERSION})
    (folder/'catalog.json').write_text(json.dumps(entries,ensure_ascii=False,indent=2),'utf-8',newline='\n')
    approved['doc/catalog.json']=hashlib.sha256((folder/'catalog.json').read_bytes()).hexdigest()
    images={image for text in documents.values() for image in re.findall(r'assets/guides/([\w-]+\.png)',text)}
    registered={item['file']:item for item in capture.get('screenshots',[])}
    for image in sorted(images):
        if image not in registered or not (guide_assets/image).is_file():
            raise ValueError('缺少新版截图：'+image)
        target=folder/'assets/guides'/image;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(guide_assets/image,target)
        approved['doc/assets/guides/'+image]=hashlib.sha256(target.read_bytes()).hexdigest()
    target=folder/'assets/guides/manifest.json'
    target.write_text(json.dumps({'publication_version':VERSION,'screenshots':[{'file':image,'demo':True,'publication_version':VERSION,'title':registered[image].get('title',image[:-4])} for image in sorted(images)]},ensure_ascii=False,indent=2),'utf-8',newline='\n')
    approved['doc/assets/guides/manifest.json']=hashlib.sha256(target.read_bytes()).hexdigest()
    for name in ('laura-demo.mp4','laura-demo-poster.jpg'):
        target=destination/'frontend/public/media'/name;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source/'frontend/public/media'/name,target)
        approved['frontend/public/media/'+name]=hashlib.sha256(target.read_bytes()).hexdigest()
    return approved
