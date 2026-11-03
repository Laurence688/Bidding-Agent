/** Pure decimal/rational calculations. Floating point is used only for presentation. */
type Q={n:bigint;d:bigint};
const q=(n:bigint,d=1n):Q=>({n,d});
const add=(a:Q,b:Q)=>q(a.n*b.d+b.n*a.d,a.d*b.d);
const mul=(a:Q,b:Q)=>q(a.n*b.n,a.d*b.d);
const div=(a:Q,b:Q)=>{if(b.n===0n)throw Error('分母必须大于零');return q(a.n*b.d,a.d*b.n);};
const cmp=(a:Q,b:Q)=>a.n*b.d-b.n*a.d;
const number=(a:Q)=>Number(a.n)/Number(a.d);
const floor=(a:Q)=>a.n/a.d;
const ceil=(a:Q)=>(a.n+a.d-1n)/a.d;
const sum=(a:Q[])=>a.reduce(add,q(0n));
function decimal(value:unknown,label='数值'):Q|null{const s=String(value??'').trim();if(!s)return null;if(!/^\d+(\.\d{1,6})?$/.test(s))throw Error(`${label}必须为正数，最多六位小数`);const [a,b='']=s.split('.');const v=q(BigInt(a+b),10n**BigInt(b.length));if(v.n<=0n)throw Error(`${label}必须大于零`);return v;}
function integer(value:unknown,label:string,min=0){if(typeof value!=='number'||!Number.isSafeInteger(value)||value<min)throw Error(`${label}必须是${min?'正':'非负'}整数数字`);return value;}
export const RATES=[1,10,25,50,100,400] as const;
export interface PortGroup {rate:number;count:number;target:string;customMB:string;limit?:number}
export interface Card {id:string;name:string;model:'packets'|'fixed';packetCount:string;fixedMB:string;allocation:'time'|'bytes'|'custom';groups:PortGroup[];maxLength?:number}
export interface Device {id:string;name:string;cards:Card[]}
export type Status='satisfied'|'insufficient'|'pending';
function groupsChecked(groups:{rate:number;count:number;limit?:number}[],forward=false){if(!Array.isArray(groups)||!groups.length)throw Error('至少需要一个端口组');for(const g of groups){integer(g.count,'端口数');if(g.limit!==undefined&&g.count>integer(g.limit,'物理端口上限'))throw Error('激活端口数不能超过物理端口上限');if(typeof g.rate!=='number'||(forward?!decimal(g.rate):!RATES.includes(g.rate as typeof RATES[number])))throw Error(forward?'速率必须为正数数字（最多六位小数）':'速率必须为 GE、10GE、25GE、50GE、100GE、400GE');}if(!groups.some(g=>g.count>0))throw Error('至少启用一个端口');}
export function forwarding(input:{length:number;overhead:boolean;groups:{rate:number;count:number}[]}){
 const length=integer(input.length,'包长',input.overhead?64:1);groupsChecked(input.groups,true);
 const bits=BigInt(length+(input.overhead?20:0))*8n;
 const groups=input.groups.map(g=>{const pps=div(mul(decimal(g.rate)!,q(1000000000n)),q(bits));return {...g,pps:number(pps),mpps:number(div(pps,q(1000000n))),totalMpps:number(div(mul(pps,q(BigInt(g.count))),q(1000000n)))};});
 const total=sum(input.groups.map(g=>div(mul(decimal(g.rate)!,q(BigInt(g.count)*1000n)),q(bits))));
 return {length,overhead:input.overhead,bitsPerFrame:Number(bits),groups,totalMpps:number(total),steps:[`1 Gbps = 10⁹ bit/s；各组速率 × 10⁹。`,`每帧 ${length}${input.overhead?' + 8 + 12':'（不计线路开销）'} Bytes × 8 = ${bits} bit。`,`单口 pps = 速率 bit/s ÷ 每帧 bit；Mpps = pps ÷ 10⁶。`,`整机单方向理论线速需求 = Σ(端口数 × 单口 Mpps) = ${number(total)} Mpps；不额外乘 2，并非实测转发能力。`]};
}
export interface GroupResult extends PortGroup {timeMS:number|null;satisfied:boolean|null;exclusiveCount:number|null}
export interface CardResult {card:Card;capacityMB:number|null;requiredMB:number|null;allocatedMB:number|null;marginMB:number|null;status:Status;groups:GroupResult[];minimumLength:number|null;minimumStatus:'satisfied'|'exceeds-maximum'|'fixed-capacity'|'allocation-insufficient'|'pending';steps:string[]}
export function evaluateCard(card:Card,length:number):CardResult{
 integer(length,'包长',1);groupsChecked(card.groups);
 if(!['packets','fixed'].includes(card.model)||!['time','bytes','custom'].includes(card.allocation))throw Error('未知容量或分配模型');
 if(card.maxLength!==undefined){integer(card.maxLength,'最大包长',1);if(length>card.maxLength)throw Error('当前包长超过允许的最大包长');}
 const active=card.groups.filter(g=>g.count>0),count=active.reduce((a,g)=>a+g.count,0),bandwidth=sum(active.map(g=>q(BigInt(g.count)*BigInt(g.rate))));
 const packets=card.model==='packets'?decimal(card.packetCount,'总包数'):null,fixed=card.model==='fixed'?decimal(card.fixedMB,'固定容量 MB'):null;
 if(card.model==='packets'&&packets&&packets.d!==1n)throw Error('总包数必须是正整数');
 const capacity=card.model==='packets'?(packets?div(mul(packets,q(BigInt(length))),q(1000000n)):null):fixed;
 const targets=active.map(g=>decimal(g.target,'目标时间')),custom=active.map(g=>card.allocation==='custom'?decimal(g.customMB,'每口缓存 MB'):null);
 const missing=!capacity||targets.some(v=>!v)||(card.allocation==='custom'&&custom.some(v=>!v));
 let required:Q|null=null,allocated:Q|null=null;
 if(targets.every(v=>!!v)){
  const demands=active.map((g,i)=>mul(q(BigInt(g.rate)),targets[i]!));
  required=card.allocation==='bytes'?div(mul(q(BigInt(count)),demands.reduce((a,b)=>cmp(a,b)>=0n?a:b)),q(8n)):div(sum(demands.map((v,i)=>mul(v,q(BigInt(active[i].count))))),q(8n));
 }
 if(card.allocation==='custom'&&custom.every(v=>!!v))allocated=sum(custom.map((v,i)=>mul(v!,q(BigInt(active[i].count)))));
 else if(capacity)allocated=capacity;
 const results:GroupResult[]=card.groups.map(g=>{
  const target=decimal(g.target,'目标时间');
  const physical=integer(g.limit??g.count,'物理端口上限');
  const supported=capacity&&target?floor(div(mul(q(8n),capacity),mul(q(BigInt(g.rate)),target))):null;
  const exclusiveCount=supported===null?null:Number(supported>BigInt(physical)?BigInt(physical):supported);
  if(g.count===0)return {...g,timeMS:null,satisfied:null,exclusiveCount};
  const c=card.allocation==='custom'?decimal(g.customMB,'每口缓存 MB'):null;
  const time=card.allocation==='custom'?(c?div(mul(q(8n),c),q(BigInt(g.rate))):null):capacity?div(mul(q(8n),capacity),card.allocation==='bytes'?q(BigInt(count)*BigInt(g.rate)):bandwidth):null;
  return {...g,timeMS:time?number(time):null,satisfied:time&&target?cmp(time,target)>=0n:null,exclusiveCount};
 });
 const poolOK=!!capacity&&!!allocated&&cmp(allocated,capacity)<=0n;
 const status:Status=missing?'pending':poolOK&&results.every(g=>g.count===0||g.satisfied)?'satisfied':'insufficient';
 // Minimum length is the demand-based re-plan; it does not silently change a custom allocation.
 const minimumLength=packets&&required&&card.model==='packets'?Number(ceil(div(mul(required,q(1000000n)),packets))):null;
 const minimumStatus:CardResult['minimumStatus']=missing?'pending':card.allocation==='custom'&&results.some(g=>g.satisfied===false)?'allocation-insufficient':card.model==='fixed'?(status==='satisfied'?'satisfied':'fixed-capacity'):minimumLength!==null&&card.maxLength!==undefined&&minimumLength>card.maxLength?'exceeds-maximum':'satisfied';
 const margin=capacity&&required?add(capacity,q(-required.n,required.d)):null;
 return {card,capacityMB:capacity?number(capacity):null,requiredMB:required?number(required):null,allocatedMB:allocated?number(allocated):null,marginMB:margin?number(margin):null,status,groups:results,minimumLength,minimumStatus,steps:[`十进制：1 MB = 10⁶ Bytes；1 Gbps = 10⁹ bit/s；1 s = 1000 ms。`,`本卡容量 ${card.model==='packets'?`${card.packetCount} × ${length} ÷ 10⁶`:`固定 ${card.fixedMB}`} = ${capacity?number(capacity):'待补参数'} MB；不加 20 Bytes 线路开销。`,`本卡 ${count} 口，Σ(n × r) = ${number(bandwidth)} Gbps；缓存不能跨线卡借用。`,`所需容量 ${card.allocation==='bytes'?'P × max(r × t) ÷ 8':'Σ(n × r × t) ÷ 8'} = ${required?number(required):'待补参数'} MB。`,`${card.allocation==='custom'?'每口 tᵢ = 8cᵢ ÷ rᵢ，并核对 Σ(nᵢcᵢ) ≤ C':card.allocation==='bytes'?'每口 tᵢ = 8C ÷ (P × rᵢ)':'等时间 t = 8C ÷ Σ(nᵢrᵢ)'}；逐档比较目标，结果为理论缓存时间。`,`最小包长按目标重新规划：ceil(10⁶ × C需 ÷ N) = ${minimumLength??'不适用'} Bytes；自定义每口容量不会自动增加。`]};
}
export function evaluateDevice(device:Device,length:number){const cards=device.cards.map(c=>evaluateCard(c,length));const status:Status=!cards.length||cards.some(c=>c.status==='pending')?'pending':cards.every(c=>c.status==='satisfied')?'satisfied':'insufficient';return {device,cards,status};}
export function distributeCard(card:Card,count:number,allocations?:number[][]):Card[]{
 integer(count,'线卡数',1);groupsChecked(card.groups);
 if(!allocations&&card.groups.some(g=>g.count%count!==0))throw Error('不能整除：请填写实际逐卡整数分配');
 if(allocations&&(allocations.length!==count||allocations.some(row=>row.length!==card.groups.length)))throw Error('逐卡分配维度不匹配');
 if(allocations)for(let j=0;j<card.groups.length;j++){const total=allocations.reduce((s,row)=>s+integer(row[j],'逐卡端口数'),0);if(total!==card.groups[j].count)throw Error('逐卡分配之和必须等于整机端口数');}
 return Array.from({length:count},(_,i)=>({...card,id:`${card.id}-${i+1}`,name:`${card.name} ${i+1}`,groups:card.groups.map((g,j)=>{const assigned=allocations?allocations[i][j]:g.count/count;return {...g,count:assigned,limit:assigned};})}));
}
