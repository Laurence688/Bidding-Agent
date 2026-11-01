from app.schemas.core import Claim, QAResult, EvidenceRef
from app.schemas.model_outputs import QAOutput
from app.model_gateway.contracts import ModelRequest
from app.workflows.prompts import render_prompt
from app.workflows.routing import product_conflicts, untrusted_instruction
from app.rag.admission import admit_evidence


def answer_product(raw_text, context, settings, gateway, retrieval, knowledge, history=None, bundle=None):
    from app.telemetry.tracing import Recorder
    from langsmith import tracing_context
    with tracing_context(enabled=False), Recorder(gateway.repo, settings).span(context.run_id + ':qa', 'Product QA',
        {'raw_text': raw_text, 'history': history or []}, metadata={'run_id': context.run_id, 'session_id': getattr(context, 'session_id', None), 'mode': 'PRODUCT_QA'}) as span:
        result = _answer_product(raw_text, context, settings, gateway, retrieval, knowledge, history, bundle)
        span.finish(result.model_dump(mode='json'))
        return result


def _answer_product(raw_text, context, settings, gateway, retrieval, knowledge, history=None, bundle=None):
    from app.services.activity import ActivityService
    activity=ActivityService(gateway.repo)
    with activity.step(context.run_id,getattr(context,'job_id',None),'qa_answer','qa'):
        return _answer_product_impl(raw_text,context,settings,gateway,retrieval,knowledge,history,bundle)


def _answer_product_impl(raw_text, context, settings, gateway, retrieval, knowledge, history=None, bundle=None):
    if product_conflicts(raw_text, context.product_id, settings.products) or untrusted_instruction(raw_text):
        return QAResult(answer='请确认任务型号与问题内容。', evidence_refs=[], uncertainties=['PRODUCT_CONFLICT_OR_UNTRUSTED_INPUT'])
    claim = Claim(clause_id='qa', text=raw_text, source_spans=[])
    from app.services.activity import ActivityService
    activity=ActivityService(gateway.repo)
    with activity.step(context.run_id,getattr(context,'job_id',None),'retrieve','qa'):
        refs = retrieval.retrieve(claim, context.product_id, context.knowledge_snapshot_id) if context.knowledge_snapshot_id else []
    refs = [r for r in admit_evidence(refs, context, knowledge, products=settings.products).admitted if not untrusted_instruction(r.quote)]
    if not refs:
        return QAResult(answer='目前没有找到可核验的产品证据，无法确认。', evidence_refs=[], uncertainties=['EVIDENCE_MISSING'])
    if getattr(context,'task_id',None):
        from app.services.context_management import ContextService
        messages=ContextService(gateway.repo,settings).prepare(raw_text,[r.model_dump(mode='json') for r in refs],context,gateway,bundle)
    else:
        messages, _ = render_prompt('product_qa', {'raw_text': raw_text, 'evidence': [r.model_dump(mode='json') for r in refs], 'history': history or []}, QAOutput.model_json_schema(), bundle)
    with activity.step(context.run_id,getattr(context,'job_id',None),'product_qa','qa'):
        result = gateway.invoke(ModelRequest(context.run_id, 'qa', '__clause__', 'product_qa', messages, QAOutput, context.config_snapshot_id))
    if result.error_code:
        if result.error_code == 'EXECUTION_OFFLINE':
            from app.domain.errors import DomainError
            raise DomainError('EXECUTION_OFFLINE','全局离线限制已暂停云端问答，请恢复权限后显式恢复任务',409)
        return QAResult(answer='模型调用未完成，请检查配置与运行记录。', evidence_refs=[], uncertainties=[result.error_code])
    with activity.step(context.run_id,getattr(context,'job_id',None),'verify_citations','qa'):
        allowed = {r.chunk_id: r for r in refs}
        cited = []
        for item in result.payload['citations']:
            if item['chunk_id'] not in allowed:
                return QAResult(answer='回答引用未通过核验，无法确认。', evidence_refs=[], uncertainties=['CITATION_INVALID'])
            ref = allowed[item['chunk_id']].model_copy(update={'quote': item['quote']})
            if not knowledge.verify_reference(ref):
                return QAResult(answer='回答引用未通过核验，无法确认。', evidence_refs=[], uncertainties=['CITATION_INVALID'])
            cited.append(ref)
        if not cited:
            return QAResult(answer='回答未给出可核验来源，无法确认。', evidence_refs=[], uncertainties=['CITATION_INVALID'])
        return QAResult(answer=result.payload['answer'], evidence_refs=cited, uncertainties=result.payload['uncertainties'])
