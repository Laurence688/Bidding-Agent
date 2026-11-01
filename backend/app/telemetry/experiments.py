from app.telemetry.storage import records
from uuid import uuid5, NAMESPACE_URL
from app.telemetry.tracing import Recorder
from app.evaluation.datasets import execution_input
from app.evaluation.scoring import score


def example_id(version_id, case_id):
    return str(uuid5(NAMESPACE_URL, 'soc-example:' + version_id + ':' + case_id))


def prepare_experiment(repo, settings, ident):
    if not getattr(settings, 'langsmith', {}).get('enabled'):
        return []
    run = repo.get('evaluation_run', ident)
    dataset = repo.get('dataset_version', run['dataset_version_id'])
    recorder = Recorder(repo, settings)
    mirror = recorder.enqueue(dataset['id'], 'dataset', {'name': 'soc-dataset-' + dataset['id'], 'version_id': dataset['id']}, ident)
    examples = []
    for index, case in enumerate(dataset['cases']):
        case_id = case.get('case_id') or 'legacy-' + str(index)
        examples.append(recorder.enqueue(example_id(dataset['id'], case_id), 'example',
            {'id': example_id(dataset['id'], case_id), 'dataset_name': mirror['payload']['name'],
             'inputs': execution_input(case), 'outputs': case['gold'], 'case_id': case_id}, ident, [mirror['id']])['id'])
    project = recorder.enqueue(ident, 'experiment', {'name': 'soc-experiment-' + ident,
        'dataset_name': mirror['payload']['name'], 'metadata': {'local_evaluation_id': ident,
        'dataset_version_id': dataset['id'], 'configs': run['configs'], 'provenance': run.get('provenance', {})}}, ident, [mirror['id'], *examples])
    return [project['id']]


def mirror_experiment(repo, settings, ident):
    if not getattr(settings, 'langsmith', {}).get('enabled'):
        return
    run = repo.get('evaluation_run', ident)
    prepare_experiment(repo, settings, ident)
    mirror_scores(repo, settings, run, {'id': 'initial', 'report': run['report']})
    traces = [i['id'] for i in records(repo, 'cloud_outbox') if i.get('run_id') in
              {r['id'] for r in repo.list('evaluation_case_run', parent_id=ident, limit=100000)}]
    Recorder(repo, settings).enqueue(ident, 'experiment_end', {'name': 'soc-experiment-' + ident,
        'status': run['status'], 'report': run['report']}, ident, traces + prepare_experiment(repo, settings, ident))


def mirror_scores(repo, settings, run, score_record):
    if not getattr(settings, 'langsmith', {}).get('enabled'):
        return
    dataset = repo.get('dataset_version', run['dataset_version_id'])
    recorder = Recorder(repo, settings)
    items = records(repo, 'cloud_outbox')
    for result in run.get('results', []):
        execution_id = result.get('execution_id')
        roots = [i for i in items if i['operation'] == 'trace' and i.get('run_id') == execution_id and not i['payload'].get('parent_run_id')]
        case = next((c for c in dataset['cases'] if c.get('case_id') == result.get('case_id')), None)
        if not roots or not case:
            continue
        report = score([case], [result])
        for metric in ('accuracy', 'citation_correctness', 'citation_recall', 'claim_coverage', 'parameter_exact_match'):
            if report.get(metric) is not None:
                key = run['id'] + ':' + str(score_record['id']) + ':' + result['case_id'] + ':' + metric
                recorder.enqueue(key, 'score', {'id': str(uuid5(NAMESPACE_URL, key)), 'run_id': roots[0]['payload']['id'],
                    'key': metric + ':' + str(score_record['id']), 'score': report[metric], 'scoring_version': report['scoring_version']}, run['id'], [roots[0]['id']])


def deliver_operation(client, item):
    p = item['payload']
    operation = item['operation']
    if operation == 'dataset':
        if client.has_dataset(dataset_name=p['name']):
            dataset = client.read_dataset(dataset_name=p['name'])
        else:
            dataset = client.create_dataset(p['name'], metadata={'local_version_id': p['version_id']})
        return {'id': str(dataset.id)}
    if operation == 'example':
        try:
            existing = client.read_example(p['id'])
            if existing.inputs != p['inputs'] or existing.outputs != p['outputs']:
                raise ValueError('CLOUD_EXAMPLE_CONFLICT')
        except Exception as exc:
            if type(exc).__name__ != 'LangSmithNotFoundError':
                raise
            client.create_example(dataset_name=p['dataset_name'], example_id=p['id'], inputs=p['inputs'], outputs=p['outputs'], metadata={'case_id': p['case_id']})
        return {'id': p['id']}
    if operation == 'experiment':
        ds = client.read_dataset(dataset_name=p['dataset_name'])
        project = client.create_project(p['name'], upsert=True, reference_dataset_id=ds.id, metadata=p['metadata'])
        return {'id': str(project.id)}
    if operation == 'experiment_end':
        from datetime import datetime, timezone
        project = client.read_project(project_name=p['name'])
        client.update_project(project.id, metadata={**(project.extra or {}).get('metadata', {}), 'status': p['status'], 'report': p['report']}, end_time=datetime.now(timezone.utc))
        return {'id': str(project.id)}
    if operation == 'score':
        try:
            client.create_feedback(p['run_id'], key=p['key'], score=p['score'], feedback_id=p['id'],
                source_info={'scoring_version': p['scoring_version']}, stop_after_attempt=1, extend_trace_retention=False)
        except Exception as exc:
            if type(exc).__name__ != 'LangSmithConflictError':
                raise
        return {'id': p['id']}
    if operation == 'annotation':
        client.add_runs_to_annotation_queue(p['queue_id'], run_ids=p['trace_ids'])
        return {'id': p['queue_id']}
    if operation == 'feedback_pull':
        from app.telemetry.feedback import FeedbackService
        feedback = [f.model_dump(mode='json') for f in client.list_feedback(run_ids=p['trace_ids'])]
        # Local persistence is performed by SyncWorker after transport returns.
        return {'feedback': feedback}
    raise ValueError('UNKNOWN_CLOUD_OPERATION')
