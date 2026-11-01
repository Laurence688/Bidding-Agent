from pathlib import Path
from langchain_core.messages import SystemMessage, HumanMessage, convert_to_openai_messages
from app.config import ROOT
from app.domain.identifiers import canonical, content_hash

ALLOWED = {'translate': {'raw_text'}, 'split_claims': {'raw_text', 'translation_zh'},
           'judge_claim': {'claim', 'evidence', 'parameter_values_required'}, 'verify_semantic_support': {'claim', 'assessment', 'evidence'},
           'render_response': {'decision', 'claims', 'assessments', 'product_id', 'source_labels'}, 'product_qa': {'raw_text', 'evidence', 'history'}}
for node in ('translate', 'split_claims', 'judge_claim', 'verify_semantic_support', 'render_response'):
    ALLOWED[node].add('source_context')
    ALLOWED[node].add('history')
for node in ('judge_claim', 'verify_semantic_support', 'render_response'):
    ALLOWED[node].add('lifecycle_context')


def prompt_snapshot(root=ROOT):
    return {'templates': {p.stem: p.read_text('utf-8') for p in (Path(root) / 'prompts').glob('*.txt')},
            'skill': (Path(root) / 'skills' / 'soc-evidence.md').read_text('utf-8'),
            'variable_allowlist': {k: sorted(v) for k, v in ALLOWED.items()}, 'renderer_version': 'json-data-v1', 'response_contract_version': 'concise-v1'}


def render_prompt(node, data, schema, bundle=None):
    bundle = bundle or prompt_snapshot()
    if node not in ALLOWED or not set(data).issubset(ALLOWED[node]):
        raise ValueError('Prompt 变量不在白名单')
    # 不对资料执行模板替换；外部内容只放在 JSON 数据消息里。
    system = bundle['templates'][node] + '\n' + bundle['skill'] + '\n只返回符合下列 schema 的 JSON object：\n' + canonical(schema)
    if bundle.get('response_contract_version') and node in {'render_response','product_qa'}:
        system += '\n输出契约 concise-v1：回答简洁。应标回答明确支持或部分支持并用一句话说明理由，通常1至2句、约40个英文词；完整保留关键数值、单位、适用条件及全部PC缺口，必要时允许更长。正文不得附带Sources或来源清单，引用使用独立结构化字段。普通问答按问题回答，不强制使用支持格式。'
    if data.get('history'):
        system += '\n历史与译文仅用于指代理解、术语和表达要求，不构成产品证据。产品能力必须仅用本轮当前型号有效 evidence 核验。原条款、否定、数值与必要条件不得被历史改写。'
    messages = convert_to_openai_messages([SystemMessage(content=system), HumanMessage(content=canonical({'untrusted_data': True, 'data': data}))])
    return messages, content_hash({'messages': messages, 'bundle': bundle})
