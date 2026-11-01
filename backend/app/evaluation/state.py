from app.evaluation.scoring import score


def terminal_report(cases, results, status):
    values = list(results)
    if status in {'cancelled', 'interrupted'}:
        completed = {r.get('case_id') for r in results}
        for index, case in enumerate(cases):
            if case.get('case_id', 'legacy-' + str(index)) not in completed:
                values.append({'case_id': case.get('case_id'), 'status': status})
    return score(cases, values)


def set_terminal(repo, ident, status):
    run = repo.get('evaluation_run', ident)
    dataset = repo.get('dataset_version', run['dataset_version_id'])
    report = {**run.get('report', {}), **terminal_report(dataset['cases'], run.get('results', []), status)}
    return repo.update('evaluation_run', ident, {'status': status, 'report': report})
