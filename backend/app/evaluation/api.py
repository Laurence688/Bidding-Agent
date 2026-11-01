from fastapi import Request
from pydantic import BaseModel, Field
from app.schemas.core import StrictModel
from app.evaluation.datasets import DatasetService, EvaluationCase
from app.evaluation.executor import EvaluationExecutor


class DatasetCreate(StrictModel):
    name: str = Field(min_length=1, max_length=100)


class EvaluationStart(StrictModel):
    dataset_version_id: str


def install_evaluation_routes(app, repo, settings, idem):
    @app.get('/api/v1/evaluation/datasets')
    def datasets():
        return {'items': repo.list('evaluation_dataset')}

    @app.get('/api/v1/evaluation/datasets/{dataset_id}/versions')
    def versions(dataset_id: str):
        repo.get('evaluation_dataset', dataset_id)
        return {'items': repo.list('dataset_version', parent_id=dataset_id, limit=100000)}

    @app.get('/api/v1/evaluation/runs')
    def runs():
        return {'items': repo.list('evaluation_run', limit=100000)}

    @app.post('/api/v1/evaluation/runs/{evaluation_run_id}/rescore', status_code=201)
    def rescore(evaluation_run_id: str, request: Request):
        return idem(request, '', 'rescore:' + evaluation_run_id, {}, lambda: EvaluationExecutor(repo, settings).rescore(evaluation_run_id))

    @app.get('/api/v1/evaluation/runs/{evaluation_run_id}/scores')
    def scores(evaluation_run_id: str):
        repo.get('evaluation_run', evaluation_run_id)
        return {'items': repo.list('evaluation_score', parent_id=evaluation_run_id)}

    @app.post('/api/v1/evaluation/datasets', status_code=201)
    def create(body: DatasetCreate):
        return DatasetService(repo).create(body.name)

    @app.post('/api/v1/evaluation/datasets/{dataset_id}/cases', status_code=201)
    def cases(dataset_id: str, body: list[EvaluationCase]):
        return DatasetService(repo).add_cases(dataset_id, [c.model_dump() for c in body])

    @app.post('/api/v1/evaluation/runs', status_code=202)
    def start(body: EvaluationStart, request: Request):
        return idem(request, '', 'evaluation', body.model_dump(), lambda: EvaluationExecutor(repo, settings).enqueue(body.dataset_version_id))

    @app.get('/api/v1/evaluation/runs/{evaluation_run_id}')
    def detail(evaluation_run_id: str):
        return repo.get('evaluation_run', evaluation_run_id)
