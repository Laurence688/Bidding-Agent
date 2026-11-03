import type {CardResult} from './network-core';
import {fmt} from './shared-ui';
export function minimumLengthLabel(r:CardResult){if(r.minimumStatus==='pending')return '待补参数';if(r.card.model==='fixed')return r.status==='satisfied'?'固定 MB 容量已满足，包长反算不适用':'包长反算不适用；无法仅靠改变包长满足，请调整总容量或每口分配';if(r.minimumStatus==='fixed-capacity')return '无法仅靠改变包长满足';if(r.minimumStatus==='exceeds-maximum')return `${fmt(r.minimumLength)} Bytes，超过允许最大包长，不可满足`;if(r.minimumStatus==='allocation-insufficient')return `${fmt(r.minimumLength)} Bytes；仍需调整每口自定义分配`;return `${fmt(r.minimumLength)} Bytes`;}
