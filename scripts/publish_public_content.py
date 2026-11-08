"""Publish explicitly authored public documentation; never copy local archives."""
from pathlib import Path
import hashlib
import json
import shutil

EXTRA_DOCS = {
    '03-评测与观测/01-评测.md': '''# 评测

评测用固定案例检查 Laura 的结论、证据和交付内容。每条案例有稳定 ID。保存新版案例集会产生新的数据版本，人工标定的 Gold 与被测模型的输入分开。

## 评分与对照

- 分别记录成功、失败、取消和未执行。业务 UNKNOWN 表示证据不足或条件未决，执行失败单独计入。
- 比较四态、错误肯定、Claim 覆盖、引用命中和参数。引用是否确实支持结论、英文是否可交付，还需人工核对。
- 同模型对照使用同一组案例、知识版本和模型设置，按案例比较完整流程和基线流程。将调用次数、耗时和已知 Token 与质量结果分别列出。

重评分读取已保存结果。固定集执行仍有运行记录读取缺口，当前不能据此报告已完成的业务基准或同模型对照结果。

[Harness](../02-架构与技术/07-Harness.md) · [观测](02-观测.md)
''',
    '03-评测与观测/02-观测.md': '''# 观测

观测记录作业进度、失败位置、结果来源、模型尝试和人工操作。定位问题时可先按项目、会话和运行筛选，再检查条款与节点的记录。

## 指标口径

- FC、PC、NC、UNKNOWN 是当前机器结论的分布。准确率需要人工标定的固定样本。
- 任务墙钟时间、节点耗时和模型累计耗时分别展示。重试和补检计入相关尝试，缺失 Token 按未知处理。
- 人工批注、批准和机器初始结果分别保存，便于比较修改前后和追溯来源。

## 追踪与同步

启用 LangSmith 记录后，SOC 条款与产品问答分别形成 Trace，SOC 节点与模型尝试形成相应的 Span。业务日志和统计在本机独立保存。LangSmith 默认关闭，外发由独立同步程序按显式配置执行。

[评测](01-评测.md) · [Agent Loop](../02-架构与技术/08-Agent%20Loop.md)
''',
}


def publish_readme_previews(destination):
    """Keep the GitHub overview illustrated with already-reviewed demo assets."""
    path = Path(destination) / 'README.md'
    if not path.is_file():
        return
    start, end = '<!-- LAURA-PREVIEWS:START -->', '<!-- LAURA-PREVIEWS:END -->'
    text = path.read_text('utf-8')
    if start in text and end in text:
        before, rest = text.split(start, 1)
        text = before + rest.split(end, 1)[1].lstrip('\n')
    section = '''<!-- LAURA-PREVIEWS:START -->
[下载 Windows 免安装包](https://github.com/Laurence688/Bidding-Agent/releases) · [完整使用指南](doc/00-使用指南/00-入门总览.md) · [观看功能演示](frontend/public/media/laura-demo.mp4)

## 界面与主要流程

以下截图使用合成演示数据，仅展示界面与操作流程；示例中的版本、结论与耗时不代表真实项目效果。

### 1. 从工作台开始

在本地浏览器中完成产品问答、SOC 应答、Excel 批量处理与人工审核。

![Laura 首页与投标工作流程概览](doc/assets/guides/intro-open.png)

### 2. 准备并发布知识库

上传 Word、PDF 或 Excel，预览切分、构建索引，再确认发布可供项目使用的版本。

![知识库从上传、切分、向量化到确认发布的步骤](doc/assets/guides/kb-publish.png)

### 3. 沿回答引用核对来源

打开引用即可查看来源片段及版本，帮助人工核对回答依据。

![产品问答与 Excel 来源单元格定位示例](doc/assets/guides/qa-source.png)

### 4. 审核应答，再导出结果

逐条核对英文口径、证据与四态结论，满足审核和导出条件后生成原表副本。

![Excel 应答审核详情与证据核对界面](doc/assets/guides/excel-review.png)
<!-- LAURA-PREVIEWS:END -->

'''
    anchor = '## 双击启动（推荐）'
    if anchor in text:
        text = text.replace(anchor, section + anchor, 1)
    else:
        text = text.rstrip() + '\n\n' + section
    path.write_text(text, 'utf-8', newline='\n')


def publish_branding(source, destination):
    source,destination=Path(source),Path(destination)
    # Publish the explicitly approved feedback address, without local-only Zmail wording.
    contact=destination/'frontend/src/components/HomeContact.tsx'
    contact.parent.mkdir(parents=True,exist_ok=True)
    contact.write_text('''import {useState} from 'react';
import {Button,Modal,Typography} from 'antd';
import {MailOutlined} from '@ant-design/icons';
import './home-contact.css';

const email='li.zhichao5@zte.com.cn';

export default function HomeContact(){
 const [open,setOpen]=useState(false);
 return <div className="home-contact">
  <Button type="link" icon={<MailOutlined aria-hidden="true"/>} onClick={()=>setOpen(true)}>
   <span>反馈与建议</span><span className="home-contact-address">{email}</span>
  </Button>
  <Modal open={open} title="反馈与建议" onCancel={()=>setOpen(false)} footer={<Button type="primary" onClick={()=>setOpen(false)}>知道了</Button>}>
   <div className="home-contact-details">
    <p>如有意见或建议，欢迎发送邮件至以下邮箱。感谢你的反馈。</p>
    <Typography.Text copyable={{text:email,tooltips:['复制邮箱','已复制']}}><a href={`mailto:${email}`}>{email}</a></Typography.Text>
    <p><a href="https://github.com/Laurence688/Bidding-Agent/issues" target="_blank" rel="noopener noreferrer">也可以在 GitHub 提交反馈与建议</a></p>
   </div>
  </Modal>
 </div>;
}
''','utf-8',newline='\n')
    style=destination/'frontend/src/components/home-contact.css'
    shutil.copyfile(source/'frontend/src/components/home-contact.css',style)
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
    from public_documentation import GUIDE_CAPTURE_VERSION, VERSION, source_documents
    source,destination=Path(source),Path(destination)
    guide_assets=Path(guide_assets) if guide_assets is not None else source/'.cache/public-guides-v04'
    if not (guide_assets/'manifest.json').is_file():
        raise ValueError('缺少已审核合成截图登记文件，请先准备 '+GUIDE_CAPTURE_VERSION+' 使用指南截图')
    capture=json.loads((guide_assets/'manifest.json').read_text('utf-8'))
    if capture.get('publication_version')!=GUIDE_CAPTURE_VERSION:
        raise ValueError('截图版本不匹配，禁止回退复制旧截图')
    folder=destination/'doc';folder.mkdir(parents=True,exist_ok=True)
    catalog=json.loads((source/'doc/catalog.json').read_text('utf-8'))
    metadata={item['path']:item for item in catalog}
    extras=dict(EXTRA_DOCS)
    documents={**source_documents(source,extras),**{name:text.replace('公司 API','内网API') for name,text in extras.items()}}
    documents={'README.md':'# Laura 文档中心\n\n出版版本：'+VERSION+'\n\n完整使用指南、产品路线、架构与技术，以及评测和观测说明。\n\n'+'\n'.join('- ['+name.split('/')[-1][:-3]+']('+name.replace(' ', '%20')+')' for name in documents)+'\n\n首页视频和配图为合成演示，不代表真实业务结果或模型质量。\n',**documents}
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
    publish_readme_previews(destination)
    return approved
