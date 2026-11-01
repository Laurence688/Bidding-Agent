"""Allowlisted read-only documentation and side-effect-free offline Markdown/SVG renderer."""
from pathlib import Path
from html import escape
import re, json, hashlib, unicodedata
from urllib.parse import unquote, urlsplit
from collections import defaultdict, deque
from markdown_it import MarkdownIt
from app.domain.errors import DomainError

# A display projection only: source Markdown and original catalog paths stay private.
# Link and asset resolution must use those original paths before this projection.
def presentation_source(value):
    # This guide provenance is useful in the local source, not in a user tutorial.
    return re.sub(r'(?m)^(截图日期：[^\n]*?。)\s*适用界面：Laura 当前工作区[^\n]*$',r'\1适用界面：Laura。',value)


def public_text(value,markers=()):
    # Inspect escaped URLs, but never insert their decoded delimiters into HTML.
    def escaped_token(hit):
        token=hit[0]
        if '%' not in token:return token
        decoded=unquote(token)
        return '[开发链接已隐藏]' if ('codex' in decoded.casefold() or any(marker.casefold() in decoded.casefold() for marker in markers) or re.search(r'[A-Za-z]:[\\/]|/(?:home|Users)/',decoded)) else token
    value=re.sub(r'[^\s`<>"\x27\[\]()]+',escaped_token,value)
    value=re.sub(r'(?i)(?:codex|feature|bugfix|hotfix|release)/[\w./-]+','[开发分支已隐藏]',value)
    value=re.sub(r'(?i)codex','开发工具',value)
    value=re.sub(r'(?<![\w])(?:\.cache|outputs)[\\/][^\s`<>"\x27|，。；（）)\]]*','[本地检查资料]',value)
    value=re.sub(r'(?i)(\b(?:branch|commit|revision|git[_-]?hash)\s*(?:=|:|：)\s*)[^\s&<>"\x27`，。；）]+',r'\1[开发标识已隐藏]',value)
    value=re.sub(r'((?:工作分支|实现分支|分支|提交号|提交|起点|基线)\s*[：:=]?\s*[`\x27"]?(?:<(?:code|strong|em)>)*)(?:[0-9a-fA-F]{7,40}\b|[A-Za-z][\w.-]*/[\w./-]+)',r'\1[开发标识已隐藏]',value)
    value=re.sub(r'(?i)(?<![\w])(?:[a-z]:[\\/]|\\\\[^\\\s]+\\)[^\r\n`<>"\x27|，。；（）)\]]*','[本机路径已隐藏]',value)
    value=re.sub(r'(?<![\w:])/(?:home|Users|workspace|workspaces|mnt|tmp|private|var/folders)/[^\s`<>"\x27|，。；（）)\]]*','[本机路径已隐藏]',value)
    for marker in sorted(markers,key=len,reverse=True):value=re.sub(re.escape(marker),'[开发标识已隐藏]',value,flags=re.I)
    return value


def public_value(value,markers=()):
    if isinstance(value,str):return public_text(value,markers)
    if isinstance(value,list):return [public_value(x,markers) for x in value]
    if isinstance(value,dict):return {key:public_value(item,markers) for key,item in value.items()}
    return value
def wrap(text,limit=27):
    lines=[]; line=''; count=0
    for ch in text:
        size=2 if unicodedata.east_asian_width(ch) in 'WF' else 1
        if count+size>limit:
            lines.append(line); line=''; count=0
        line+=ch; count+=size
    if line:lines.append(line)
    return lines

def diagram(source,uid,title="流程图"):
    # 本轮六张图只使用基本节点/有向边；拒绝静默丢弃不识别的语法。
    nodes={}; edges=[]; decisions=set()
    def node(raw):
        raw=raw.strip()
        hit=re.fullmatch(r'(\w+)(?:\[(.*)\]|\{(.*)\})?',raw)
        assert hit, f'Unsupported node {raw}'
        key,box,choice=hit.groups()
        label=box if box is not None else choice
        if label is not None:
            if label.startswith('(') and label.endswith(')'):label=label[1:-1]
            nodes[key]=label
        else:nodes.setdefault(key,key)
        if choice is not None:decisions.add(key)
        return key
    for line in source.splitlines():
        line=line.strip()
        if not line or line.startswith('flowchart'):continue
        hit=re.fullmatch(r'(.+?)\s+--\s+(.+?)\s+-->\s+(.+)',line)
        if hit:left,label,right=hit.groups()
        else:
            hit=re.fullmatch(r'(.+?)\s+-->(?:\|([^|]+)\|)?\s+(.+)',line)
            assert hit, f'Unsupported edge {line}'
            left,label,right=hit.groups()
        edges.append((node(left),node(right),label or ''))
    # 去掉 DFS 回边后分层，回边单独走外侧，保留全部原始连接及分支标注。
    adj=defaultdict(list)
    for a,b,label in edges:adj[a].append(b)
    colors={}; back=set()
    def visit(a):
        colors[a]=1
        for b in adj[a]:
            if colors.get(b)==1:back.add((a,b))
            elif not colors.get(b):visit(b)
        colors[a]=2
    for n in nodes:
        if not colors.get(n):visit(n)
    degree={n:0 for n in nodes}; ranks={n:0 for n in nodes}
    for a,b,_ in edges:
        if (a,b) not in back:degree[b]+=1
    queue=deque(n for n in nodes if degree[n]==0)
    while queue:
        a=queue.popleft()
        for b in adj[a]:
            if (a,b) in back:continue
            ranks[b]=max(ranks[b],ranks[a]+1);degree[b]-=1
            if degree[b]==0:queue.append(b)
    layers=defaultdict(list)
    for n in nodes:layers[ranks[n]].append(n)
    bw,bh,xgap,ygap=250,104,34,68
    width=max(730,max(map(len,layers.values()))*(bw+xgap)+100)
    height=(max(layers)+1)*(bh+ygap)+35
    pos={}
    for rank,group in layers.items():
        start=(width-(len(group)*bw+(len(group)-1)*xgap))/2
        for i,n in enumerate(group):pos[n]=(start+i*(bw+xgap),25+rank*(bh+ygap))
    compact=set(nodes)==set('V T S R A J C Q X SR AG EN VO CR'.split())
    if compact:
        # 单条执行主图折行为四列，保证桌面能同时看见完整闭环与补检索分支。
        bw,bh,width,height=230,76,1120,560
        grid={'V':(0,0),'T':(1,0),'S':(2,0),'R':(3,0),'A':(3,1),'J':(2,1),'C':(1,1),'Q':(0,1),'X':(0,2),'SR':(1,2),'AG':(2,2),'EN':(3,2),'VO':(3,3),'CR':(2,3)}
        pos={n:(34+c*270,25+r*132) for n,(c,r) in grid.items()}
    out=[f'<svg role="img" aria-label="{escape(title)}流程图" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg"><defs><marker id="{uid}-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="#547398"/></marker></defs>']
    for i,(a,b,label) in enumerate(edges):
        ax,ay=pos[a];bx,by=pos[b]
        if compact and (a,b)==('SR','A'):
            d=f'M{ax+bw/2},{ay} V{ay-24} H1090 V{by+bh/2} H{bx+bw}'
            lx,ly=1093,by+bh/2-10
        elif compact and (a,b)==('X','AG'):
            yy=ay+bh+26
            d=f'M{ax+bw/2},{ay+bh} V{yy} H{bx+bw/2} V{by+bh}'
            lx,ly=(ax+bx+bw)/2,yy-7
        elif compact and ay==by:
            x1=ax+bw if bx>ax else ax;x2=bx if bx>ax else bx+bw
            d=f'M{x1},{ay+bh/2} H{x2}'
            lx,ly=(x1+x2)/2-6,ay+bh/2-10
        elif (a,b) in back or ranks[b]<=ranks[a]:
            side=25+12*(i%3)
            d=f'M{ax},{ay+bh/2} H{side} V{by+bh/2} H{bx}'
            lx,ly=side+10,(ay+by)/2+bh/2
        else:
            x1,y1=ax+bw/2,ay+bh;x2,y2=bx+bw/2,by
            mid=(y1+y2)/2
            d=f'M{x1},{y1} C{x1},{mid} {x2},{mid} {x2},{y2}'
            lx,ly=(x1+x2)/2+9,mid-7
        out.append(f'<path d="{d}" fill="none" stroke="#6f89a7" stroke-width="1.7" marker-end="url(#{uid}-arrow)"/>')
        if label:out.append(f'<text x="{lx}" y="{ly}" fill="#084acb" font-size="14" stroke="white" stroke-width="5" paint-order="stroke">{escape(label)}</text>')
    aliases={'validate_input':'输入检查','translate':'辅助翻译','split_claims':'拆分并检查要求覆盖','retrieve':'逐 Claim 检索','admit_evidence':'证据准入','judge_claim':'逐 Claim 判定','verify_citations':'程序引用校验','verify_semantic_support':'语义核验','supplement_retrieve':'一次定向补检索','aggregate':'四态聚合','render_response':'生成口径 / 固定 NC','validate_output':'输出规则与正文核验','commit_result':'保存结果，等待审核'}
    for n,label in nodes.items():
        x,y=pos[n]; lines=([aliases[label],label] if compact and label in aliases else wrap(label))
        assert len(lines)<=5,(label,lines)
        fill='#eaf1ff' if n not in decisions else '#fff5dd'
        border='#8facd3' if n not in decisions else '#cfaa50'
        out.append(f'<g data-node="{escape(n)}"><title>{escape(label)}</title><rect x="{x}" y="{y}" width="{bw}" height="{bh}" rx="9" fill="{fill}" stroke="{border}"/><text text-anchor="middle" fill="#142c48" font-size="15">')
        for i,line in enumerate(lines):
            ty=y+bh/2-(len(lines)-1)*10+5+i*20
            out.append(f'<tspan x="{x+bw/2}" y="{ty}">{escape(line)}</tspan>')
        out.append('</text></g>')
    out.append('</svg>')
    return '<figure class="diagram"><figcaption>流程图 · 可在框内滚动查看 · '+str(len(nodes))+' 个图形节点</figcaption><div class="diagram-scroll">'+''.join(out)+'</div><details><summary>查看对应 Mermaid 源码</summary><pre><code>'+escape(source)+'</code></pre></details></figure>'


def render_document(raw, ident, catalog, assets=None, include_archive=True):
    md=MarkdownIt('commonmark', {'html':False}).enable('table')
    default_validate=md.validateLink
    md.validateLink=lambda url: url.startswith('laura:') or default_validate(url)
    toc=[]
    def heading(tokens,idx,options,env):
        anchor=f'h{len(toc)+1}'
        tokens[idx].attrSet('id',anchor)
        toc.append({'id':anchor,'level':int(tokens[idx].tag[1:]),'title':tokens[idx+1].content})
        return md.renderer.renderToken(tokens,idx,options,env)
    def link(tokens,idx,options,env):
        token=tokens[idx];url=unquote(token.attrGet('href') or '')
        current=next((x['path'] for x in catalog if x['id']==ident),'README.md')
        from posixpath import normpath,join,dirname
        target=normpath(join(dirname(current),url.split('#')[0]))
        hit=next((x for x in catalog if x['path']==target),None)
        if url.startswith('#'):
            token.attrSet('href',f'#docs/{ident}?heading='+url[1:])
        elif url.startswith('laura:') and url[6:] in {'models','projects','knowledge','qa','soc','excel','translation','review'}:
            token.attrSet('href','#');token.attrSet('data-guide-action',url[6:])
        elif not include_archive and hit and hit.get('scope')=='archive':
            token.attrSet('href','#');token.attrSet('data-local-archive','true')
            token.attrSet('title','归档资料仅在本地保留')
        elif hit:token.attrSet('href','#docs/'+hit['id']+('?heading='+url.split('#',1)[1] if '#' in url else ''))
        elif urlsplit(url).scheme in ('http','https'):
            token.attrSet('target','_blank');token.attrSet('rel','noopener noreferrer')
        elif url.endswith('.html'):
            token.attrSet('href','#docs')
        else:
            token.attrSet('href','#');token.attrSet('data-local-path',url)
            token.attrSet('title','点击复制本地路径')
        return md.renderer.renderToken(tokens,idx,options,env)
    old_fence=md.renderer.rules['fence']
    def fence(tokens,idx,options,env):
        t=tokens[idx]
        if t.info.strip()=='mermaid':
            try:return diagram(t.content,f'{ident}-graph-{idx}',ident)
            except (AssertionError,ValueError,KeyError):
                return '<p>该图语法暂不支持，请查看流程图源码。</p><pre><code>'+escape(t.content)+'</code></pre>'
        return old_fence(tokens,idx,options,env)
    def image(tokens,idx,options,env):
        token=tokens[idx]
        from posixpath import normpath,join,dirname
        current=next((x['path'] for x in catalog if x['id']==ident),'README.md')
        url=unquote(token.attrGet('src') or '')
        target=normpath(join(dirname(current),url))
        asset=next((a for a in (assets or []) if a['path']==target),None)
        if asset and not urlsplit(url).scheme and not url.startswith('//'):
            return '<figure class="guide-shot"><img data-doc-asset="'+asset['id']+'" alt="'+escape(token.content,quote=True)+'" loading="lazy" tabindex="0" role="button" aria-label="放大截图：'+escape(token.content,quote=True)+'"><figcaption>'+escape(token.content)+'</figcaption></figure>'
        return '<span class="image-placeholder">[图片已阻止自动加载：'+escape(token.content)+']</span>'
    md.renderer.rules.update(heading_open=heading,link_open=link,fence=fence,image=image)
    body=md.render(raw).replace('<table>','<div class="table-scroll"><table>').replace('</table>','</table></div>')
    return body,toc

def natural_key(value):
    return [int(x) if x.isdigit() else x.casefold() for x in re.split(r'(\d+)',value)]

class DocumentService:
    """One filesystem discovery snapshot per request. Catalog supplies legacy IDs only."""
    def __init__(self,root,include_archive=True):
        self.include_archive=include_archive
        self.folder=(Path(root)/'doc').resolve()
        try:legacy=json.loads((self.folder/'catalog.json').read_text('utf-8'))
        except (OSError,ValueError):legacy=[]
        old={x['path']:x for x in legacy if 'path' in x}
        self.catalog=[];self.assets=[];self._redactions=set()
        for path in sorted(self.folder.rglob('*.md'),key=lambda p:(p.relative_to(self.folder).as_posix()!='README.md',natural_key(p.relative_to(self.folder).as_posix()))):
            relative=path.relative_to(self.folder)
            if any(part.startswith('.') or part in {'assets','工具','scripts','node_modules','__pycache__'} for part in relative.parts[:-1]):continue
            if path.is_symlink() or not path.resolve().is_relative_to(self.folder):continue
            if any(parent.is_symlink() for parent in path.parents if parent!=self.folder and parent.is_relative_to(self.folder)):continue
            name=relative.as_posix();previous=old.get(name,{})
            try:source=path.read_text('utf-8')
            except (OSError,UnicodeError):source=''
            self._redactions.update(re.findall(r'(?i)\b(?:codex|feature|bugfix|hotfix|release)/([\w./-]+)',source))
            self._redactions.update(re.findall(r'(?:提交号|提交|起点|基线|commit|revision)\s*[：:=]?\s*[`\x27"]?([a-fA-F0-9]{7,40})\b',source))
            ident=previous.get('id') or ('intro' if name=='README.md' else 'doc-'+hashlib.sha256(name.encode()).hexdigest()[:20])
            self.catalog.append({**previous,'id':ident,'path':name,'title':'文档总览' if name=='README.md' else path.stem,
                'scope':'archive' if relative.parts[0]=='归档' else 'guide' if relative.parts[0]=='00-使用指南' else 'current',
                'directory':' / '.join(relative.parts[:-1])})
        asset_folder=self.folder/'assets'/'guides'
        try:registered=json.loads((asset_folder/'manifest.json').read_text('utf-8')).get('screenshots',[])
        except (OSError,ValueError):registered=[]
        for entry in registered:
            candidate=asset_folder/entry.get('file','')
            if candidate.suffix.lower() not in {'.png','.webp'} or candidate.is_symlink():continue
            resolved=candidate.resolve()
            if not resolved.is_relative_to(asset_folder.resolve()) or not resolved.is_relative_to(self.folder) or not resolved.is_file():continue
            name=candidate.relative_to(self.folder).as_posix()
            if not any(asset['path']==name for asset in self.assets):self.assets.append({'id':hashlib.sha256(name.encode()).hexdigest()[:24],'path':name})
    def _read(self,item):
        path=(self.folder/item['path']).resolve()
        if not path.is_relative_to(self.folder) or path.suffix!='.md':raise DomainError('DOC_NOT_FOUND','文档已删除、移动或不存在，请返回目录重新选择',404)
        try:return path,path.read_text('utf-8')
        except (OSError,UnicodeError):raise DomainError('DOC_NOT_FOUND','文档已删除、移动或不存在，请返回目录重新选择',404)
    def list(self,scope='current',q=''):
        if scope not in ('current','archive','guide','all','active'):raise DomainError('INVALID_SCOPE','文档范围无效',422)
        items=[]
        for item in self.catalog:
            if not self.include_archive and item['scope']=='archive':continue
            if scope=='active' and item['scope']=='archive':continue
            if scope not in {'all','active'} and item['scope']!=scope:continue
            try:_,raw=self._read(item)
            except DomainError:continue
            raw=public_text(presentation_source(raw),self._redactions);display=public_value(item,self._redactions)
            if q.casefold() not in (display['title']+' '+display['directory']+' '+display['path']+' '+raw).casefold():continue
            summary=re.sub(r'[#*`>|\n]',' ',raw);summary=re.sub(r'\s+',' ',summary).strip()
            offset=max(0,summary.casefold().find(q.casefold())-45) if q else 0
            items.append({**display,'summary':summary[offset:offset+160]})
        return {'items':items}
    def tree(self):
        nodes=[];folders={};fingerprints=[]
        for item in self.catalog:
            if not self.include_archive and item['scope']=='archive':continue
            parts=Path(item['path']).parts;children=nodes
            for depth,name in enumerate(parts[:-1]):
                key='/'.join(parts[:depth+1])
                if key not in folders:
                    node={'kind':'folder','name':name,'path':key,'scope':item['scope'],'children':[]};folders[key]=node;children.append(node)
                children=folders[key]['children']
            children.append({'kind':'document','name':item['title'],**item})
            try:path,raw=self._read(item);fingerprints.append((item['path'],hashlib.sha256(raw.encode()).hexdigest()))
            except DomainError:pass
        return public_value({'nodes':nodes,'items':[item for item in self.catalog if self.include_archive or item['scope']!='archive'],'version':hashlib.sha256(json.dumps(fingerprints).encode()).hexdigest()},self._redactions)
    def asset(self,ident):
        asset=next((x for x in self.assets if x['id']==ident),None)
        if not asset:raise DomainError('DOC_ASSET_NOT_FOUND','截图不存在或尚未登记',404)
        path=(self.folder/asset['path']).resolve()
        if not path.is_relative_to(self.folder/'assets'/'guides'):raise DomainError('DOC_ASSET_NOT_FOUND','截图不存在',404)
        return path
    def get(self,ident):
        item=next((x for x in self.catalog if x['id']==ident),None)
        if not item:raise DomainError('DOC_NOT_FOUND','文档已删除、移动或不存在，请返回目录重新选择',404)
        if not self.include_archive and item['scope']=='archive':raise DomainError('DOC_ARCHIVE_HIDDEN','归档文档仅在本地保留，不在文档中心展示',404)
        path,raw=self._read(item);fingerprint=hashlib.sha256(raw.encode()).hexdigest();raw=presentation_source(raw);html,toc=render_document(raw,ident,self.catalog,self.assets,self.include_archive)
        used=[asset for asset in self.assets if 'data-doc-asset="'+asset['id']+'"' in html]
        return public_value({**item,'content':raw,'html':html,'toc':toc,'assets':used,'content_hash':fingerprint,'modified_at':path.stat().st_mtime},self._redactions)
