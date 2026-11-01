from app.evaluation.state import set_terminal
import argparse
import os
import time
import psutil
from app.config import Settings
from app.persistence.repositories import Repository
from app.worker.leases import WorkerLease, reconcile_interrupted
from app.domain.errors import DomainError
from app.services.knowledge import KnowledgeService
from app.model_gateway.gateway import ModelGateway
from app.schemas.core import RunContext
from app.workflows.soc_graph import SocWorkflow
from app.workflows.qa import answer_product


class LazyEmbedding:
    def __init__(self, settings):
        self.settings, self.instance = settings, None

    def load(self):
        if self.instance is None:
            from app.rag.embeddings import LocalEmbedding
            self.instance = LocalEmbedding.from_settings(self.settings)
        return self.instance

    @property
    def fingerprint(self):
        from app.services.libraries import ProfileEmbedding, LibraryService
        return ProfileEmbedding(LibraryService(None,self.settings).default_profile(),self.settings).fingerprint

    def embed(self, texts, query=False):
        from app.services.libraries import ProfileEmbedding, LibraryService
        return ProfileEmbedding(LibraryService(None,self.settings).default_profile(),self.settings).embed(texts,query)


class WorkerRunner:
    def __init__(self, repo, settings, transport=None, retriever=None, secret_loader=None):
        self.repo, self.settings, self.transport, self.retriever = repo, settings, transport, retriever
        self.embedding = LazyEmbedding(settings)
        self.secret_loader = secret_loader or (lambda: Settings(data_dir=settings.data_dir, root=settings.root).secrets)
        self._runtime_signature=self.runtime_signature()

    def runtime_signature(self):
        paths=[self.settings.configuration_root/'.env.local',self.settings.configuration_root/'config'/'langsmith.yaml']
        return tuple((p.stat().st_mtime_ns,p.stat().st_size) if p.exists() else None for p in paths)

    def refresh_runtime(self):
        signature=self.runtime_signature()
        if signature!=self._runtime_signature:
            fresh=Settings(data_dir=self.settings.data_dir,root=self.settings.root)
            self.settings.langsmith=fresh.langsmith
            self.settings.secrets=self.secret_loader()
            self._runtime_signature=signature

    def claim(self):
        with self.repo.transaction():
            jobs = [j for j in self.repo.list('job', limit=100000) if j['status'] == 'queued' and not j.get('cancel_requested')]
            if not jobs:
                return None
            job = self.repo.update('job', jobs[0]['id'], {'status': 'running', 'owner_pid': os.getpid(), 'owner_started': psutil.Process().create_time()})
            self.repo.event(job['id'], 'job.status', {'status': 'running'})
            return job

    def step(self):
        self.refresh_runtime()
        # 密钥可轮换；模型、Prompt、检索参数仍然由该运行的不可变快照决定。
        self.settings.secrets = self.secret_loader()
        job = self.claim()
        if not job:
            return False
        try:
            if job['kind'] == 'task':
                self.execute_task(job)
            elif job['kind'] == 'document_task':
                from app.services.document_tasks import DocumentTaskService
                DocumentTaskService(self.repo,self.settings).execute(job,transport=self.transport)
                return True
            elif job['kind'] == 'context_compression':
                from app.services.context_management import ContextService
                ContextService(self.repo,self.settings).execute_compression(job,self.transport)
            elif job['kind'] == 'precheck':
                from app.services.excel import ExcelService
                ExcelService(self.repo, self.settings).execute_precheck(job)
            elif job['kind'] == 'export':
                from app.services.exports import ExportService
                ExportService(self.repo, self.settings).execute(job)
            elif job['kind'] == 'review_validation':
                from app.services.reviews import ReviewService
                ReviewService(self.repo, self.settings).validate_wording(job, self.transport)
            elif job['kind'] == 'kb_build':
                from app.services.libraries import LibraryService
                LibraryService(self.repo,self.settings).execute_build(job['build_id'])
            elif job['kind'] == 'knowledge':
                from app.rag.retrieval import HybridRetriever
                knowledge = KnowledgeService.from_settings(self.repo, self.settings)
                from app.services.libraries import LibraryService
                knowledge.lifecycle_guard=lambda:LibraryService(self.repo,self.settings).check_legacy(job['product_id'],job['id'])
                knowledge.check_lifecycle(job['product_id'])
                snapshot = knowledge.ingest(job['product_id'])
                HybridRetriever(self.repo, knowledge, self.embedding).build(snapshot['id'])
            elif job['kind'] == 'evaluation':
                from app.evaluation.executor import EvaluationExecutor
                EvaluationExecutor(self.repo, self.settings, self.transport, self.retriever).execute(job)
                if self.repo.get('evaluation_run', job['evaluation_run_id'])['status'] == 'failed':
                    raise DomainError('EVALUATION_CASE_FAILED', '部分评测案例执行失败，请查看评测报告', 503)
            else:
                raise DomainError('JOB_KIND_INVALID', '未知作业类型', 422)
            current = self.repo.get('job', job['id'])
            if current.get('cancel_requested'):
                state = 'cancelled'
            else:
                state = 'waiting_review' if current.get('has_product_conflict') else 'completed'
            self.finish_job(job,state,{'stage':'finalize'},'job.status',{'status':state})
        except DomainError as exc:
            state = 'cancelled' if exc.code in {'JOB_CANCELLED','BUILD_CANCELLED'} else 'interrupted' if exc.code == 'EXECUTION_OFFLINE' else 'failed'
            self.finish_job(job,state,{'error':{'code':exc.code,'message':exc.message}},'job.error',{'code':exc.code,'message':exc.message})
        except Exception as exc:
            self.finish_job(job,'failed',{'error':{'code':'EXECUTION_FAILED','message':'执行异常，请查看本地运行记录','type':type(exc).__name__}},'job.error',{'code':'EXECUTION_FAILED'})
        if job.get('evaluation_run_id'):
            state = self.repo.get('job', job['id'])['status']
            if state in {'failed', 'cancelled', 'interrupted'}:
                set_terminal(self.repo, job['evaluation_run_id'], state)
        return True

    def finish_job(self, job, state, changes, event_type, event_data):
        with self.repo.transaction():
            self.repo.update('job',job['id'],{'status':state,**changes})
            if job.get('kind')=='task' and job.get('run_id'):
                from app.services.activity import ActivityService
                ActivityService(self.repo).finish(job['run_id'])
            self.repo.event(job['id'],event_type,event_data)

    def execute_task(self, job):
        self.refresh_runtime()
        from app.rag.retrieval import HybridRetriever
        run = self.repo.get('run', job['run_id'])
        self.repo.update('run',run['id'],{'activity_started_at':run.get('activity_started_at') or time.time(),'activity_finished_at':None})
        config = self.repo.get('config_snapshot', run['config_snapshot_id'])
        knowledge = KnowledgeService.from_settings(self.repo, self.settings)
        from app.security.execution_policy import policy_for
        gateway = ModelGateway(self.repo, config['models'], self.settings.secrets, self.transport,
            policy=lambda: policy_for(self.settings, frozen=config.get('execution_policy') or policy_for(self.settings)))
        # 运行固定检索配置，不追随全局设置漂移。
        import copy
        frozen_settings = copy.copy(self.settings)
        frozen_settings.local = {**self.settings.local, **config['retrieval']}
        frozen_settings.products = config['products']
        frozen_settings.response_product_name = config.get('response_product_name',run['product_id'])
        frozen_settings.processing = config.get('processing', {})
        frozen_settings.execution_policy = config.get('execution_policy')
        embedding = self.embedding if config['retrieval'] == {k: self.settings.local[k] for k in config['retrieval']} else LazyEmbedding(frozen_settings)
        from app.rag.library_retrieval import LibraryRetriever
        retrieval = self.retriever or LibraryRetriever(self.repo,knowledge,embedding,frozen_settings,config['retrieval']['retrieval_top_k'],config['retrieval']['rrf_k'])
        context = RunContext(run_id=run['id'], mode=run['mode'], local_user_id=self.repo.local_user_id, project_id=run['project_id'],
            product_id=run['product_id'], session_id=run.get('session_id'), task_id=run.get('task_id'), config_snapshot_id=config['id'], knowledge_snapshot_id=run['knowledge_snapshot_id'], job_id=job['id'])
        bindings = self.repo.list('run_binding', parent_id=run['id'], limit=100000)
        snapshot = self.repo.get('input_snapshot', run['input_snapshot_id'], run['project_id'])
        context = RunContext.model_validate({**context.model_dump(), 'lifecycle_context': snapshot.get('lifecycle_context', {})})
        if run['mode'] == 'PRODUCT_QA':
            # A committed result is the recovery checkpoint; restoring messages never invokes a model.
            if run.get('qa_result'):
                from app.schemas.core import QAResult
                from pydantic import ValidationError
                try:saved=QAResult.model_validate(run['qa_result'])
                except ValidationError:saved=None
                if saved is not None:
                    with self.repo.transaction():
                        self._message(run,saved.answer,saved.model_dump(mode='json'))
                        self.repo.update('job',job['id'],{'processed_items':1})
                    return
            from app.services.context_management import ContextService
            service=ContextService(self.repo,self.settings)
            history=service.history(service.freeze_run(run,config))
            answer = answer_product(bindings[0]['raw_text'], context, frozen_settings, gateway, retrieval, knowledge,
                [{'role': m['role'], 'text': m['text']} for m in history], config['prompts'])
            if self.repo.get('job',job['id']).get('cancel_requested'):
                raise DomainError('JOB_CANCELLED','任务已停止',409)
            with self.repo.transaction():
                self.repo.update('run', run['id'], {'qa_result': answer.model_dump(mode='json')})
                self._message(run, answer.answer, answer.model_dump(mode='json'))
                self.repo.update('job', job['id'], {'processed_items': 1})
            return
        workflow = SocWorkflow(self.repo, frozen_settings, gateway, retrieval, knowledge, config['prompts'])
        completed, conflicts = 0, False
        for binding in bindings:
            if self.repo.get('job', job['id']).get('cancel_requested'):
                raise DomainError('JOB_CANCELLED', '任务已取消', 409)
            if binding.get('result_id'):
                result = self.repo.get('clause_result', binding['result_id'])['result']
                from app.telemetry.tracing import Recorder
                with Recorder(self.repo, frozen_settings).span(run['id'] + ':' + binding['clause_id'], 'SOC clause',
                    {'raw_text': binding['raw_text'], 'source_context': binding.get('source_context', {})},
                    metadata={'run_id': run['id'], 'clause_id': binding['clause_id'], 'result_id': binding['result_id'],
                        'cache_reuse': True, 'reused_from_run_id': binding.get('inherited_from_run_id')}) as span:
                    span.finish(result)
            else:
                clause_context = context.model_copy(update={'source_context': binding.get('source_context', {})})
                result = workflow.run_clause(binding['clause_id'], binding['raw_text'], clause_context).model_dump(mode='json')
            conflicts = conflicts or 'PRODUCT_CONFLICT' in result['reason_codes']
            completed += 1
            self.repo.update('job', job['id'], {'processed_items': completed, 'has_product_conflict': conflicts})
        self._message(run, f'已处理 {completed} 条。请进入审核台检查结论与来源。', {'processed_items': completed})

    def _message(self, run, text, payload):
        if run.get('session_id'):
            message_type={'PRODUCT_QA':'qa_result','SOC_SINGLE':'soc_result','SOC_BATCH':'batch_result'}[run['mode']]
            with self.repo.transaction():
                existing = [m for m in self.repo.list('session_message', run['project_id'], run['session_id'],limit=100000) if m.get('run_id') == run['id'] and m.get('type')==message_type]
                if not existing:
                    self.repo.put('session_message', {'role': 'assistant', 'type':message_type, 'text': text, 'payload': payload, 'task_id': run['task_id'], 'run_id': run['id']}, run['project_id'], run['session_id'])

    def run(self, once=False):
        with WorkerLease(self.repo):
            reconcile_interrupted(self.repo)
            while True:
                worked = self.step()
                if once:
                    return
                if not worked:
                    from app.rag.resource_pool import embedding_pool,index_pool
                    embedding_pool(str(self.settings.data_dir)).expire()
                    index_pool(str(self.settings.data_dir)).expire()
                    from app.local_runtime.configuration import LocalRuntimeSettings
                    LocalRuntimeSettings(self.settings).manager().release_idle(self.settings.local_models.get('idle_seconds',600))
                    time.sleep(0.5)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    settings = Settings()
    WorkerRunner(Repository(settings.data_dir), settings).run(args.once)


if __name__ == '__main__':
    main()
