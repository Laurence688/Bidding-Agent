"""Source-preserving editorial projection for the versioned public manual.

Original Markdown stays local and unchanged. Only named historical sections are
removed; current tables, contracts, diagrams and operational instructions survive.
"""
import posixpath
import re
from pathlib import Path
from urllib.parse import unquote

VERSION = 'v0.4.6'
GUIDE_CAPTURE_VERSION = 'v0.4.2'
PUBLIC_SOURCE_DOCS = (
    '00-使用指南/00-入门总览.md',
    '00-使用指南/01-模型准备.md',
    '00-使用指南/02-项目与产品.md',
    '00-使用指南/03-构建知识库.md',
    '00-使用指南/04-首次产品问答.md',
    '00-使用指南/05-单条应标.md',
    '00-使用指南/06-Excel应标审核与导出.md',
    '00-使用指南/07-单条翻译.md',
    '00-使用指南/08-常见问题.md',
    '01-产品与路线/PRD.md',
    '01-产品与路线/计划书.md',
    '02-架构与技术/01-Prompt.md',
    '02-架构与技术/02-Skill.md',
    '02-架构与技术/03-Tool.md',
    '02-架构与技术/04-RAG.md',
    '02-架构与技术/05-记忆管理.md',
    '02-架构与技术/06-上下文管理.md',
    '02-架构与技术/07-Harness.md',
    '02-架构与技术/08-Agent Loop.md',
    '02-架构与技术/参考资料/架构设计.md',
    '02-架构与技术/参考资料/技术栈选型.md',
)


def section(text, title, replacement=''):
    lines=text.splitlines(keepends=True)
    for start,line in enumerate(lines):
        if line.strip()!=title:
            continue
        level=len(line)-len(line.lstrip('#'))
        end=start+1
        while end<len(lines):
            hit=re.match(r'^(#{1,6}) ',lines[end])
            if hit and len(hit[1])<=level:
                break
            end+=1
        return ''.join(lines[:start])+replacement+'\n\n'+''.join(lines[end:])
    raise ValueError('原文章节已变化，需重新审阅：'+title)


PLAN_INTRO='''# SOC 投标助手当前计划与验收顺序

本计划维护当前产品路线、交付条件与后续验收顺序；具体需求见 [PRD](PRD.md)，系统执行与持久化见 [架构设计](../02-架构与技术/参考资料/架构设计.md)。路线中的目标不表示业务质量、性能或兼容性已经验收。软件可启动、合成流程完成和真实资料验证分别记录，不互相替代。

## 1. 当前阶段与路线

当前产品具备项目与会话、结构化知识、单条和 Excel SOC、人工审核、运行观测、提示词管理、范围修订及参数约束；独立标书拆解和翻译使用各自任务契约。生成使用用户所选 API，本地向量化和索引负责检索，不自动跨供应商切换。用户准备自己的产品与资料，公开发行不预装业务知识库和历史结果。

后续路线按“输入与可比性 → 完整固定样本 → 同输入全链路质量 → 交付与目标机”推进。优先保护原始要求、有效证据、必要条件、四态含义与审核门禁；不通过放宽 UNKNOWN 或删减事实换取表面速度。当前实现不等于对任意资料、工作簿、供应商或电脑作出质量承诺。

## 2. 评测与可比性要求

| 主题 | 交付要求 | 核对条件 |
|---|---|---|
| 处理预算冻结 | 评测使用入队时的候选数、字符预算和节点策略 | 排队后改变全局设置，不改变该实验的固定输入与策略 |
| 完整输入 | 样本保留 Excel 要求值、原表上下文和必要生命周期条件 | 金标准只交给评分器，不进入执行输入；缺字段明确标记 |
| 机器与人工结果 | 运行报告沿结果版本追溯机器初始值，人工修改独立记录 | 继承与缺失基线使用一致规则，不把同一结果重复计数 |
| 可复现边界 | 保存代码、Schema、提示词渲染器和依赖身份 | 配置哈希相同不等于封存历史执行环境，报告必须指出实现变化 |
| 质量判据 | 先定义必要要求、证据资格和可接受输出 | 人工标注分歧可回查，不以 FC 占比或 UNKNOWN 下降代替正确性 |

业务样本、真实供应商调用及固定范围的完整实验须另行审定。观测与评测提供核对机制，不能自动生成“达到业务门槛”的结论。
'''

PLAN_DELIVERY='''## 5. 交付检查与持续维护

软件验证分别覆盖后端规则、前端状态、浏览器交互、真实资料和供应商调用。记录适用版本、输入范围、排除项、失败与限制；只对实际完成的层次作结论。备用接口的真实手动切换、原生 Excel 展示、完整人工金标准和业务门槛应独立验收。

便携包内置 Python、Node.js、固定后端依赖与已构建网页；首次向量模型准备仍需联网。目标电脑需核对解压路径、运行时加载、默认浏览器、启动/停止、存储权限和更新恢复，不能把开发电脑上的成功视为任意电脑兼容。保留配置、知识库、历史结果和用户密钥，更新不得静默覆盖用户数据。

当前文档按单主题维护：PRD 管需求，架构管执行，RAG 管证据检索，观测管指标，技术栈管配置，本页管后续工作。使用指南说明可执行操作，公开版本只包含当前说明和必要质量边界，不分发私人执行档案。
'''

PLAN_CHECKLIST='''### 6.1 能力交付清单

- 界面与工作区：统一中文控件、导航、输入框、表格、抽屉和折叠表单；保留未保存草稿与历史链接，正确区分当前和历史运行。
- 审核与日志：服务器按当前结果筛选和分页，来源仅引用最终证据，任务详情固定运行版本；聊天展示不代替审核资格。
- 文档任务：使用独立输入、输出、上传用途和 Worker 分发，不能全局放宽问答或 SOC 的型号与证据约束。
- 分段恢复：冻结文件、段落位置、语言、术语、模型与提示词；结果和完成标记原子保存，取消、重试与部分完成均可解释。
- 翻译交付：单条文本可复制，多文件输出双语 DOCX/XLSX 或 ZIP；原件不覆盖，数字、单位、否定、术语与条件需人工核对。
- 标书拆解：分类提取项目、资格、时间、技术、商务交付、评分及重点关注信息；逐项保留原文引用和覆盖状态，不自动判断废标。
- 上下文与数据管理：入队冻结消息清单与摘要版本，最近完整问答及当前约束保留；摘要失败保留原文，超硬窗口明确阻断。
- 知识库与项目：预览、构建、发布、绑定分别确认；回收和恢复遵守版本与任务保护，历史引用、原件和共享产品保留。
- 演示与可访问性：首页合成示例提供暂停、重播和减少动画的静态降级；示例不是业务结果或速度承诺。
- 独立业务验收：用人工审定样本核对拆解、翻译与 SOC；同输入质量对照通过后再评价成本、耗时和目标机可用性。
'''

PLAN_NEXT='''## 7. 专项归并与下一步

操作入口见 [使用指南](../00-使用指南/00-入门总览.md)，知识切分与版本规则见 [RAG 设计](../02-架构与技术/04-RAG.md)，固定集和统计口径见 [评测](../03-评测与观测/01-评测.md) 与 [观测](../03-评测与观测/02-观测.md)。

优先完成完整输入的人工固定集和同口径业务评测，核对真实供应商、明确开启的外部观测同步、目标机安装与恢复边界。真实建库能执行只说明构建链可运行，尚不能宣布检索质量提高。界面风格调整不替代业务改进；新知识版本仍须使用者确认发布，整理文档不触发建库、发布或模型请求。
'''


def edit_plan(text):
    text=PLAN_INTRO+'\n'+text[text.index('## 3. 再建立'):]
    text=section(text,'## 5. 交付检查与持续维护',PLAN_DELIVERY)
    text=section(text,'### 6.1 执行清单',PLAN_CHECKLIST)
    text=section(text,'## 7. 专项实施归并与下一步',PLAN_NEXT)
    text=text.replace('原 C117/C127/C151/C159，含','覆盖').replace('原 C66/C82，以及','覆盖').replace('原 C215、C526，以及','覆盖').replace('、原 C608 的条件遗漏','及条件遗漏')
    text=re.sub(r'历史 327→327→205[^\n]*\n','',text)
    text=text.replace('（第 1—13 项，软件已实现）','').replace('本轮页面升级批次','能力交付分组')
    text=text.replace('原文档基线已完成；本轮追加软件实现和隔离技术测试，不调用真实模型','使用指南、产品契约和架构说明一致；不以文档完整代替业务验收')
    text=text.replace('约 14 秒演示','合成流程演示')
    return text


def edit_design(name,text):
    if name.endswith('/PRD.md'):
        text=text[text.index('## 1. 产品目标'):]
        text='# SOC 投标助手产品需求文档\n\n本文说明当前用户流程、产品契约与尚未完成的能力；工程验证不能代替真实业务质量验收。\n\n'+text
        text=section(text,'## 9. 设计来源与历史证据','## 9. 文档与后续工作\n\n当前需求、机制和原则由本组文档共同维护，后续工作见 [计划书](计划书.md)。历史运行和人工审核记录保留原版本，不能通过文档修订回写旧结论。')
        text=text.replace('预置 M6000-SE、6120H-S，支持用户新增型号','产品目录初始为空，使用者登记自己的型号')
        text=text.replace('页面切换/关闭不取消持久队列；机器或 worker 停止时不会继续执行。','页面切换不取消持久队列；使用图标启动时关闭最后一个 Laura 标签页后，刷新缓冲期结束将停止本地服务。机器或 Worker 停止时不会继续执行。')
    elif name.endswith('/计划书.md'):
        return edit_plan(text)
    elif name.endswith('/04-RAG.md'):
        text=text[text.index('**当前方案'):]
        text='# RAG 设计\n\n'+text
        text=section(text,'### 9.2 已保存的结构化基线')
        text=section(text,'### 3.1 来源边界','''### 3.1 来源边界

资料须由使用者确认使用权、具体产品及来源类型。产品说明、功能清单、标准清单、规划与其他文档分别标记；同系列或相似型号资料不能自动合并到本次产品。Excel 原始字段是事实来源，打印 PDF 只补充固定页码，不能替代原始单元格含义。公开发行不包含作者的原始资料、历史建库报告或预置产品知识。

`KnowledgeService.ingest` 只接受授权路径，复制到受控来源目录并复核 SHA-256。相同文件哈希、产品、文档类型和解析指纹可复用有效来源版本，撤销来源不能自动重建成有效证据。固定来源清单的编号范围属于发布约束，资料增补后须重新审查，不能为通过校验补造条目。用户新增库走第 10 节的显式预览、构建与发布链。
''')
        text=re.sub(r'^\| 已有历史资产验证.*\n|^\| 已有本地回归证据.*\n','',text,flags=re.M)
        text=text.replace('按 run＋clause＋claim＋逻辑节点累计最多 3 次；前两次主模型，第三次备模型','所选 API 新运行按 run＋clause＋claim＋逻辑节点累计最多两次总尝试，不自动切换供应商；旧主备快照仅保留兼容解释')
        text=re.sub(r'^阶段原文及真实本地向量化记录见.*$','',text,flags=re.M)
        text=re.sub(r'^默认构建脚本使用.*$','构建应使用明确的本地运行时、设备与模型指纹。向量缓存由文本和 Embedding 指纹命名，读取时校验输入与输出哈希；缓存存在不证明当前资料已发布或具备检索质量。',text,flags=re.M)
        text=text.replace('内置固定库保留；新增产品库','既有配置库仅作兼容；使用者新增产品库')
    elif name.endswith('/架构设计.md'):
        text=text[text.index('产品范围见'):]
        text='# SOC 投标助手架构设计\n\n本说明保留当前固定工作流、持久化、来源版本和后续模块的技术契约；工程验证不等于业务质量验收。\n\n'+text
        text=text.replace('2026-10-28 基线明确分开','架构明确分开')
        text=text.replace('旧版仅取最近 20 条消息的方式已由冻结清单与摘要替代；SOC 仍不使用会话历史。','会话采用冻结清单与摘要管理；问答、应标和聊天翻译共享连贯信息，产品事实与支持结论仍由本轮证据独立核验。')
        text=text.replace('仅问答提供历史压缩','问答、应标和聊天翻译共享版本化历史；SOC 原始要求与证据不参与压缩')
    elif name.endswith('/技术栈选型.md'):
        text=text[text.index('## 1. 已采用'):]
        text='# SOC 投标助手技术栈与配置\n\n本文说明当前组件、配置和取舍，依赖可安装不等于任意机器兼容或业务质量已经验收。\n\n'+text
        text=text.replace('本轮只读版本为 3.11.17，不修改系统解释器','便携发行内置 Python 3.11.17，不修改系统解释器')
        text=text.replace('`deepseek-v4-flash`','`deepseek-flash`').replace('`ark-code-latest`','`doubao-seed-2-1-pro-260915`').replace('https://ark.cn-beijing.volces.com/api/plan/v3','https://ark.cn-beijing.volces.com/api/v3')
        text=re.sub(r'^SDK 0\.14\.12 的.*$','外部 SDK 的资源接口和弃用策略需持续跟进，升级前验证项目、字段与同步兼容。SDK 维护状态不能代替真实云端可用性验证。',text,flags=re.M)
    return text


def public_document(name,text,published):
    text=edit_design(name,text)
    text=re.sub(r'<!--.*?-->','',text,flags=re.S)
    text=re.sub(r'^截图日期：.*$','',text,flags=re.M)
    text=text.replace('公司 API','内网API').replace('公司接口','内网API').replace('公司管理员','内网API管理员')
    text=text.replace('soc-parameter-scope-v6','SOC 固定工作流').replace('本轮未读取密钥','密钥仅在本机服务端处理')
    text=re.sub(r'`?codex/[\w./-]+`?','',text)
    text=re.sub(r'20\d{2}[-年/]\d{1,2}(?:[-月/]\d{1,2})?(?:日)?','本版',text)
    text=re.sub(r'（(?:本版)(?: 更新|更新|，[^）]*)?）','',text)
    text=re.sub(r'(?m)^## 本版 (.+)$',r'## \1',text)
    text=text.replace('v6','当前版本')
    text=text.replace('当前版本 在','当前版本在')
    text=text.replace('本轮未读取秘密文件或验证供应商可用性','凭据仅在本机服务端读取，供应商可用性需由使用者显式校验')
    text=text.replace('本轮只读','当前声明')
    text=text.replace('当前 `active_generation_profile_id=deepseek`；豆包、GLM 和内网API 由用户手动选择','当前生成接口以设置页的明确选择与任务快照为准；公共 API 与内网API由用户选择')
    text=text.replace('V2 默认 DeepSeek','当前使用明确选择的 API').replace('V2 默认选择 DeepSeek','当前生成接口由用户明确选择')
    text=text.replace('预置 M6000-SE、6120H-S','由使用者登记产品')
    text=text.replace('本版 进行软件回归；未调用真实供应商模型','业务质量须通过真实供应商和人工样本独立验收')
    text=text.replace('当前版本 有本地回归/历史输出回放证据，不等于新真实整批结果；','工程检查不能代替业务结论；')
    text=text.replace('当前版本 有本地回放和工程测试，尚无新的真实整批业务验收','不能以软件检查代替真实整批业务验收')
    text=text.replace('当前版本 尚无新整批业务验收结果','需用真实完整输入和人工判据独立验收')
    text=text.replace('有结构和资产用例及历史报告；当前变更不能继承未经重跑的通过结论','验证应覆盖当前来源版本；不能继承未重跑的通过结论')
    text=text.replace('有程序用例；不等于完整人工 gold 召回评测','程序规则检查不等于完整人工 gold 召回评测')
    text=text.replace('有边界测试；复杂原件、图形关系及真实模型仍需审定','复杂原件、图形关系及真实模型须独立审定')
    text=text.replace('软件已实现，本轮技术回归已完成','当前软件能力仍需独立业务验收')
    text=text.replace('本轮已实现软件能力并使用隔离合成数据验证；','')
    text=text.replace('本轮修复/验证刷新缺口与跨标签页刷新','同页与跨标签页刷新保持目录同步')
    text=text.replace('已有产品创建/全局刷新链路补同页和窗口聚焦回归','产品创建与目录刷新支持同页和窗口聚焦同步')
    text=text.replace('完整离线分发和目标机性能不属于本轮已交付架构','便携包内置运行时和依赖，首次模型下载仍需联网；目标机性能需独立验收')
    text=text.replace('可离线安装的 API 版标准包与目标电脑业务/性能验收仍待独立交付','便携包内置运行时、依赖与网页，首次模型下载仍需联网；目标电脑业务与性能需独立验收')
    text=re.sub(r'V1 曾安装本地 Qwen 生成模型及 llama\.cpp；[^。]*。','旧本地生成快照仅保留兼容读取，不提供新的本地生成入口。',text)
    text=re.sub(r'具体工件卸载和数据保护核验见[^。]*。','',text)
    text=re.sub(r'以下是 \[[^\]]+\]\([^)]*\) 已落地的增量选型；[^。]*。','以下是当前界面与独立文档任务的选型和边界。',text)
    text=text.replace('已有固定评测集和标准包待办继续保留。本轮软件验证不改变上述业务和安装交付待办状态。','评测集与目标机验收各自记录适用版本和结论。')
    # Remove only reported execution/asset history; keep current contracts in the
    # same sections, including qualifications and quality limitations.
    history_starts=('历史记录保留各日期的事实','机制与回归、浏览器验证状态见','上述 D 编号见','此表保留原核对时','上述部分工程缺口本轮已修复')
    text='\n\n'.join(p for p in text.split('\n\n') if not p.startswith(history_starts))
    text=re.sub(r'当前 DeepSeek 已有真实历史运行[^。]*。','',text)
    text=re.sub(r'DeepSeek 已有真实历史运行；[^。]*。','',text)
    text=re.sub(r'本轮[^。\n]*(?:回放|回归|验证见|证据见|已完成技术)[^。\n]*。','',text)
    text=re.sub(r'本版 (?:已恢复|已接通|已统一)[^；。\n]*[；。]','',text)
    text=text.replace('D-001/D-002/D-003','评测一致性问题').replace('D-003 有新增回归','机器与人工基线须分别保留').replace('（问题记录 D-004）','').replace('见 D-004','需说明可复现边界').replace('D-004','执行环境可复现边界')
    text=text.replace('更新：本版。','').replace('更新日期：本版。','')

    def link(hit):
        label,url=hit[1],hit[2]
        if url.startswith(('laura:','https://','http://','#')):
            return hit[0]
        target=posixpath.normpath(posixpath.join(posixpath.dirname(name),unquote(url.split('#')[0])))
        if target in published or target.startswith('assets/guides/'):
            return hit[0]
        if any(v in target for v in ('归档/','验收记录','问题记录','tests/','data/','03-评测与观测','04-界面与交互','05-模型与交付','06-质量与验收')):
            return ''
        if target.startswith('../backend/') or url.startswith('../../backend/') or url.startswith('../../frontend/') or url.startswith('../../scripts/') or url.startswith('../../config/') or url.startswith('../../Start') or url.startswith('../../README'):
            return label
        return label
    text=re.sub(r'(?<!!)\[([^\]]+)\]\(([^)]+)\)',link,text)
    text=re.sub(r'（本版[^）]*）','',text)
    text=re.sub(r'(?:详细进度与验收|新增能力的实现与验证证据|验证范围|原生 Excel 视觉验收边界|具体实现和验证状态以|相关测试入口|追踪)(?:见|：)?\s*[。，；]','',text)
    text=re.sub(r'证据见\s*[。，；]','',text)
    guide = posixpath.relpath('00-使用指南/00-入门总览.md', posixpath.dirname(name))
    text=text.replace('完整交互以  为准', f'完整交互见 [使用指南]({guide})')
    text=text.replace('| 下文源码链接 |','| 当前规则见对应设计章节 |')
    text=re.sub(r'(^\| 待验收 \|[^\n]*)\|\s*\|$',r'\1| 需独立人工样本和真实调用验收 |',text,flags=re.M)
    text=text.replace('历史“尚未调用主模型”及“全局默认 auto”已不代表现状。','')
    text=text.replace('当前固定集评测未增加新库选择器，沿用原默认产品库；新增库可通过绑定项目的新任务和库内检索测试验证。','固定集评测与业务项目范围分别管理；新增知识库应通过明确绑定的项目任务和库内检索验证，不能假设评测自动使用全部项目库。')
    text=text.replace('本轮','本次')
    text=re.sub(r'^.*(?<![\w])[A-Za-z]:[\\/].*$','',text,flags=re.M)
    text=text.replace('，见 。','。').replace('；详见。','。').replace('详见。','').replace('见 、','').replace('、 和 。','。').replace('与 。','。')
    text=re.sub(r'\[\]\([^)]*\)','',text)
    text=re.sub(r'\n{3,}','\n\n',text).strip()+'\n'
    title,rest=text.split('\n',1)
    return title+'\n\n出版版本：'+VERSION+'\n'+rest


def source_documents(source,extra_names=()):
    folder = (Path(source) / 'doc').resolve()
    names = set(PUBLIC_SOURCE_DOCS) | set(extra_names) | {'README.md'}
    result = {}
    for name in PUBLIC_SOURCE_DOCS:
        path = folder / name
        if not path.is_file() or not path.resolve().is_relative_to(folder):
            raise ValueError('缺少公开源文档或路径超出范围：' + name)
        result[name] = public_document(name, path.read_text('utf-8'), names)
    model='00-使用指南/01-模型准备.md'
    result[model]=result[model].replace('## 2. 设为当前接口','''## 2. 设为当前接口与启用内网API

内网API的地址与模型 ID 出厂为空，不从作者配置复制。先通过 [申请内网API Key](https://maas-openclaw.zte.com.cn/zte-paas-coclaw-ui/#/getkey) 获得有权使用的账号与服务信息，核对 Base URL、模型 ID 和密钥后点击“保存并启用内网API”。首次使用须阅读发送说明并明确授权；必要条款与证据将发送到所选端点，保存或恢复草稿不代表联网授权。其他公共接口仍可单独保存并设为当前接口。
''')
    intro='00-使用指南/00-入门总览.md'
    result[intro]+='''
## 安装、启动与更新

普通用户下载完整便携 ZIP 并解压后双击 Laura.exe。包内已包含 Python、Node.js、后端依赖和网页，无需手工安装这些运行环境；首次初始化仍需联网准备向量模型。保持目录完整，不单独移动启动器。打开默认浏览器后按指南准备自己的产品、资料和生成接口。

关闭最后一个 Laura 标签页后，刷新缓冲期结束将停止本地服务；普通页面切换与短暂刷新不会新建任务。再次使用时重新双击图标。停止前仍应检查进行中的任务，在途模型请求可能已经计费。

更新可从顶部或系统设置中的“检查更新”页面、包内“检查更新Laura.cmd”进入。首次从旧版升级、任务保护与失败恢复步骤见 [常见问题](08-常见问题.md)。更新不等于重新授权生成 API，本地配置、知识库和历史结果继续保存在既有目录。
'''
    faq='00-使用指南/08-常见问题.md'
    result[faq]+='''
## 8. 更新与旧版本升级

### 已使用 v0.4.2 或后续版本

从顶部或系统设置打开“检查更新”，也可双击原目录中的“检查更新Laura.cmd”。更新功能开关默认关闭。手动打开后，点击“检查更新”只查询项目 GitHub 的版本信息，不下载也不安装。需要新版时，再点击“下载新版”并确认；下载校验完成后，点击“安装并重启”再次确认才替换程序。没有后台自动检查、自动下载或自动安装；关闭开关会暂停下载。更新过程不启动业务模型请求。

检查更新需要能访问 GitHub，与生成 API 联网开关独立。关闭生成 API 联网不会替代更新服务的访问控制，检查更新也不会授予条款和证据的生成发送权限。网络或校验失败时按页面提示重试，不使用来源不明的文件绕过校验。

### 从 0.3.x 首次升级

旧版没有更新入口，需要先下载并解压一次 v0.4.2 完整包。运行新包中的“检查更新Laura.cmd”，选择原 Laura 目录，由更新工具使用新包升级原目录；无需手工搬运知识库或对话。以后使用页面或原目录脚本更新即可，不需要反复下载完整包来搬数据。

### 任务保护与数据保留

存在排队、运行或取消中任务时会阻止安装，不强制取消任务。先等待结束或在原任务中按支持的流程处理，再重新检查。安装前自动备份知识库、对话、记忆、模型及配置；保留原目录与用户状态，数据继续留在既有目录，不迁移到用户 AppData。

备份保存在原目录的 `.laura-backups`，属于本机用户数据，不上传 GitHub。更新失败时工具回滚程序及 SQLite、配置备份；失败后先查看提示并保留备份，不反复手工覆盖正在使用的数据库。此机制不构成对任意断电或磁盘损坏的恢复承诺，重要资料仍应按自己的备份制度管理。
'''
    return result
