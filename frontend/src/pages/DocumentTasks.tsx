import {useEffect, useRef, useState} from 'react';
import {Alert, Button, Checkbox, Collapse, Drawer, Empty, Input, Progress, Select, Space, Spin, Table, Tag, Typography, Upload} from 'antd';
import {UploadOutlined, DownloadOutlined, ReloadOutlined} from '@ant-design/icons';
import {api, Data, downloadDocumentArtifact} from '../api/client';
import './document-tasks.css';

type Mode = 'TRANSLATION' | 'TENDER_ANALYSIS';
type Draft = {text: string; files: Data[]; language: string; confirmed: boolean; selections: Record<string, Data>};
const drafts = new Map<string, Draft>();
const initial = (): Draft => ({text: '', files: [], language: 'zh', confirmed: false, selections: {}});
const statusName: Record<string,string> = {queued: '排队中', running: '处理中', completed: '全部完成', partial: '部分完成', failed: '执行失败', interrupted: '服务中断', cancelled: '已取消', unprocessed: '尚未处理'};
const prefix = '/document-tasks';
const categoryName:Record<string,string>={TRANSLATION_TEXT:'单条翻译',TRANSLATION_FILE:'标书翻译',TRANSLATION_UNKNOWN:'翻译',TENDER_ANALYSIS:'标书拆解'};

export default function DocumentTasks({project, mode, initialRunId, onRun, active=true}: {project: string | Data; mode: Mode; initialRunId?: string; active?: boolean; onRun?: (runId:string) => void}) {
  const projectId = typeof project === 'string' ? project : project?.id;
  const scope = `${projectId}:${mode}`;
  const [draft, setDraft] = useState<Draft>(() => drafts.get(scope) || initial());
  const [tasks, setTasks] = useState<Data[]>([]), [detail, setDetail] = useState<Data | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [source, setSource] = useState<Data | null>(null);
  const activeScope = useRef(scope), selectedRun = useRef('');
  activeScope.current = scope;
  const update = (change: Partial<Draft>) => setDraft(current => {const next = {...current, ...change}; drafts.set(scope, next); return next;});
  const load = async (expected = scope) => {
    if (!projectId) return;
    const data = await api(`${prefix}/projects/${projectId}/tasks`);
    if (activeScope.current === expected) setTasks(data.items.filter((item:Data) => item.mode === mode));
  };
  const showRun = async (id:string, notify = false) => {
    selectedRun.current = id;
    const value = await api(`${prefix}/runs/${id}`);
    if (value.run.project_id !== projectId || value.run.mode !== mode) {
      if (selectedRun.current === id && activeScope.current === scope) {setDetail(null); selectedRun.current = '';}
      throw new Error('该运行不属于当前项目或模块，请切换到对应项目后查看');
    }
    if (selectedRun.current === id && activeScope.current === scope) {setDetail(value); if (notify) onRun?.(id);}
  };
  useEffect(() => {
    setDraft(drafts.get(scope) || initial()); setTasks([]); setDetail(null); setError(''); setSource(null); selectedRun.current = '';
    let active = true;
    const refresh = async () => {try {await load(scope); const id = selectedRun.current; if (active && id) await showRun(id);} catch (e) {if (active) setError((e as Error).message);}};
    void refresh(); const timer = window.setInterval(() => void refresh(), 2500);
    return () => {active = false; window.clearInterval(timer);};
  }, [scope]);
  useEffect(() => {if (initialRunId && initialRunId !== selectedRun.current) void showRun(initialRunId).catch(e => setError(e.message));}, [scope, initialRunId]);
  const start = async () => {
    setBusy(true); setError('');
    try {
      const result = await api(`${prefix}/projects/${projectId}/tasks`, 'POST', {mode, raw_text: draft.files.length ? '' : draft.text, file_ids: draft.files.map(f => f.id), target_language: draft.language, confirmed: draft.confirmed, excel_selections: draft.selections});
      await load(); await showRun(result.run_id, true);
    } catch (e) {setError((e as Error).message);} finally {setBusy(false);}
  };
  const action = async (name:string) => {
    if (!detail) return; setBusy(true); setError('');
    try {const result = await api(`${prefix}/runs/${detail.run.id}/${name}`, 'POST', {}); await load(); await showRun(result.run_id || detail.run.id, true);} catch (e) {setError((e as Error).message);} finally {setBusy(false);}
  };
  const selectedFiles = draft.files.filter(f => f.preview?.requires_scope);
  const scopeComplete = selectedFiles.every(f => {const selection = draft.selections[f.id]; const ranges = Array.isArray(selection) ? selection : [selection]; return ranges.every(s => s?.sheet && s?.range && s?.columns?.length);});
  const ready = draft.confirmed && scopeComplete && (draft.files.length > 0 || draft.text.trim().length > 0);
  return <section className="document-tasks-page">
    <div className="page-heading"><div><p className="eyebrow">工作中心 · 独立文档任务</p><h1>{mode === 'TRANSLATION' ? (detail?categoryName[detail.display_category]??'翻译':draft.files.length?'标书翻译':'单条翻译') : '标书拆解'}</h1><p>{mode === 'TRANSLATION' ? '保留原文、位置和异常，生成可回查的双语对照。' : '按七类整理中文要点，每项附准确原文和来源位置。'}</p></div></div>
    <Alert type="info" showIcon title="处理范围与发送权限" description="原件在本机保存。开始后，确认范围内的必要正文会发送至系统设置当前选择的 API；仅使用该供应商，不自动切换。扫描件需先完成 OCR，当前不读取图片或图表含义。"/>
    {error && <Alert type="error" showIcon title={error} closable onClose={() => setError('')}/>}
    <div className="document-task-input">
      {mode === 'TRANSLATION' && <><label htmlFor="document-source">待翻译原文</label><Input.TextArea id="document-source" aria-label="待翻译原文" rows={5} value={draft.text} disabled={draft.files.length > 0} placeholder="默认英译中，也可选择中译英；或在下方上传文件" onChange={e => update({text: e.target.value, confirmed: false})}/></>}
      <Upload multiple accept=".docx,.pdf,.xlsx" fileList={draft.files.map(f => ({uid:f.id,name:f.name,status:'done' as const}))} beforeUpload={async file => {
        if (file.size > 50 * 1024 * 1024) {setError('单文件不能超过 50 MiB'); return Upload.LIST_IGNORE;}
        setBusy(true); setError(''); const originScope = scope;
        try {const form = new FormData(); form.append('file', file); const saved = await api(`${prefix}/projects/${projectId}/files`, 'POST', form);
          if (activeScope.current === originScope) setDraft(current => {const next = {...current, files: [...current.files, saved], confirmed: false}; drafts.set(scope,next); return next;});
        } catch (e) {if (activeScope.current === originScope) setError((e as Error).message);} finally {setBusy(false);} return false;
      }} onRemove={file => {update({files:draft.files.filter(f => f.id !== file.uid), confirmed:false}); return true;}}>
        <Button icon={<UploadOutlined/>} disabled={!projectId}>上传 Word / 文字型 PDF / Excel</Button>
      </Upload>
      {selectedFiles.map(file => {
        const selected = draft.selections[file.id]; const ranges:Data[] = Array.isArray(selected) ? selected : [selected || {}];
        const changeRange = (index:number, change:Data) => update({confirmed:false,selections:{...draft.selections,[file.id]:ranges.map((s,i) => i === index ? {...s,...change} : s)}});
        return <div key={file.id}><strong>{file.name} · 确认 Excel 范围</strong>{ranges.map((selectedRange,index) => <div className="document-excel-scope" key={index}>
          <Select aria-label={`${file.name} 工作表 ${index+1}`} placeholder="选择实际工作表" value={selectedRange.sheet} options={file.preview.sheets.map((s:Data) => ({value:s.name,label:`${s.name}（${s.rows}行 × ${s.columns}列）`}))} onChange={sheet => changeRange(index,{sheet,columns:[]})}/>
          <Input aria-label={`${file.name} 范围 ${index+1}`} placeholder="确认范围，例如 A2:D100" value={selectedRange.range || ''} onChange={e => changeRange(index,{range:e.target.value})}/>
          <Select aria-label={`${file.name} 内容列 ${index+1}`} mode="multiple" placeholder="选择待处理内容列" value={selectedRange.columns || []} options={Array.from({length:Math.min(file.preview.sheets.find((s:Data) => s.name === selectedRange.sheet)?.columns || 0,16384)},(_,i) => {let n=i+1,c='';while(n){n--;c=String.fromCharCode(65+n%26)+c;n=Math.floor(n/26);}return {value:c,label:c};})} onChange={columns => changeRange(index,{columns})}/>
          {ranges.length > 1 && <Button onClick={() => update({confirmed:false,selections:{...draft.selections,[file.id]:ranges.filter((_,i) => i !== index)}})}>移除此范围</Button>}
        </div>)}<Button onClick={() => update({confirmed:false,selections:{...draft.selections,[file.id]:[...ranges,{}]}})}>添加工作表或范围</Button></div>;
      })}
      {mode === 'TRANSLATION' && <Space><span>目标语言</span><Select aria-label="目标语言" value={draft.language} options={[{value:'zh',label:'中文（默认英译中）'},{value:'en',label:'英文（中译英）'}]} onChange={language => update({language,confirmed:false})}/></Space>}
      <Checkbox checked={draft.confirmed} onChange={e => update({confirmed:e.target.checked})}>我已确认语言、文件及 Excel 范围，并允许向当前所选 API 发送必要文本</Checkbox>
      <Space><Button type="primary" loading={busy} disabled={!ready || !projectId} onClick={() => void start()}>{mode === 'TRANSLATION' ? '开始翻译' : '开始拆解'}</Button><Typography.Text type="secondary">原文件不覆盖，产物不自动入库或进入应答审核。</Typography.Text></Space>
    </div>
    <div className="document-task-layout"><div><h2>任务列表</h2><Button icon={<ReloadOutlined/>} onClick={() => void load().catch(e => setError(e.message))}>刷新任务</Button>
      <Table<Data> size="small" rowKey="id" dataSource={tasks} pagination={{pageSize:8}} columns={[{title:'任务类型',dataIndex:'display_category',render:v=>categoryName[v]??'翻译'},{title:'创建时间',dataIndex:'created_at',render:value => new Date(value).toLocaleString()},{title:'状态',render:(_,task) => <Tag color={task.run.status === 'partial' ? 'orange' : undefined}>{statusName[task.run.status] || task.run.status}</Tag>},{title:'操作',render:(_,task) => <Button type="link" onClick={() => void showRun(task.active_run_id,true).catch(e => setError(e.message))}>查看结果</Button>}]}/>
    </div><div aria-live="polite"><h2>进度与结果{detail?` · ${categoryName[detail.display_category]??(mode==='TRANSLATION'?'翻译':'标书拆解')}`:''}</h2>{!detail ? <Empty description="选择任务查看运行进度与结果"/> : <>
      <Tag color={detail.run.status === 'partial' ? 'orange' : 'blue'}>{statusName[detail.run.status]}</Tag><Progress percent={Math.floor(detail.coverage.completed / Math.max(1,detail.coverage.total) * 100)} status={detail.run.status === 'failed' ? 'exception' : undefined}/>
      <p>全文解析清单 {detail.coverage.total} 段 · 已完成 {detail.coverage.completed} · 失败 {detail.coverage.failed} · 未处理 {detail.coverage.unprocessed}</p>
      {detail.run.status !== 'completed' && <Alert type="warning" title="结果尚不完整" description="失败与未处理内容不代表没有要求。部分产物均标记不完整，请核对覆盖与定位。"/>}
      {(detail.run.error_code || detail.run.artifact_error) && <Alert type="error" title={detail.run.error_code || detail.run.artifact_error} description="已保存的分段结果保留；可按状态恢复或重试。额度耗尽需要新运行。"/>}
      <Space wrap><Button disabled={busy || !['partial','failed'].includes(detail.run.status)} onClick={() => void action('retry')}>重试失败部分</Button><Button disabled={busy || !['interrupted','cancelled'].includes(detail.run.status)} onClick={() => void action('resume')}>恢复原运行</Button><Button disabled={busy || ['queued','running'].includes(detail.run.status)} onClick={() => void action('rerun')}>重新运行（新额度）</Button><Button danger disabled={busy || !['queued','running','interrupted'].includes(detail.run.status)} onClick={() => void action('cancel')}>取消任务</Button></Space>
      {detail.artifacts.map((artifact:Data) => <Button key={artifact.id} icon={<DownloadOutlined/>} onClick={() => void downloadDocumentArtifact(artifact.id,artifact.name).catch(e => setError(e.message))}>{artifact.name}</Button>)}
      <Collapse items={[{key:'files',label:'原件范围、限制与冻结术语',children:detail.files.map((file:Data) => <div key={file.id}><strong>{file.name}</strong><p>SHA-256：{file.sha256}</p><p>{file.limitations.join('；') || '原生文本'}</p><p>文件级冻结术语：{Object.entries(file.terms).map(([term,target]) => `${term} → ${target}`).join('、') || '无识别到的固定术语'}</p></div>)}]}/>
      {mode === 'TENDER_ANALYSIS' && detail.analysis && <><Alert type="info" title={detail.analysis.absence_scope} description={detail.analysis.conflict_policy}/><Collapse items={Object.entries(detail.analysis.categories).map(([category,items]) => ({key:category,label:`${category}（${(items as Data[]).length}）`,children:(items as Data[]).length ? (items as Data[]).map((item,index) => <div key={index} className="document-analysis-item"><Typography.Paragraph copyable={{text:item.summary+'\n原文：'+item.quote+'\n来源：'+item.file_name+' · '+item.location}}>{item.summary}</Typography.Paragraph><blockquote>{item.quote}</blockquote><Button type="link" onClick={() => setSource({...item, source:item.quote})}>{item.file_name} · {item.location} · 查看原文</Button></div>) : '已处理文本未发现；未处理章节不作判断。'}))}/></>}
      <Table<Data> size="small" rowKey="id" dataSource={detail.segments} pagination={{pageSize:8}} scroll={{x:600}} columns={[{title:'来源位置',render:(_,segment) => <Button type="link" onClick={() => setSource(segment)}>{segment.file_name} · {segment.location} · {segment.start}–{segment.end}</Button>},{title:'状态',render:(_,segment) => <><Tag>{statusName[segment.status]}</Tag>{segment.error_code || segment.unsupported}</>},{title:mode === 'TRANSLATION' ? '双语对照' : '提取结果',render:(_,segment) => mode === 'TRANSLATION' ? <><p>{segment.source}</p><p>{segment.result?.translation || '尚未完成'}</p>{segment.result?.flags?.map((flag:string) => <Tag color="orange" key={flag}>{flag}</Tag>)}{segment.result?.translation && <Typography.Paragraph copyable={{text:segment.source+'\n'+segment.result.translation}}>复制双语</Typography.Paragraph>}</> : `${segment.result?.items?.length ?? 0} 项${segment.status === 'completed' ? '' : '（未完成，不代表没有要求）'}`} ]}/>
    </>}</div></div>
    <Drawer title="原文与定位" open={active && !!source} onClose={() => setSource(null)}><p>{source?.file_name} · {source?.location}</p><p>字符范围：{source?.start}–{source?.end}</p><Typography.Paragraph style={{whiteSpace:'pre-wrap'}} copyable>{source?.source}</Typography.Paragraph></Drawer>
    {busy && <Spin className="document-task-busy" tip="正在保存，请稍候"><span/></Spin>}
  </section>;
}
