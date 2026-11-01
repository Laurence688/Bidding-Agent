class ProjectMemoryService:
    def __init__(self, repo):
        self.repo = repo

    def remember(self, project_id, result_id, review_id, applicability, mode):
        if mode == 'EVALUATION':
            return None
        existing = [m for m in self.repo.list('project_memory', project_id) if m['result_id'] == result_id and m['review_id'] == review_id]
        if existing:
            return existing[0]
        return self.repo.put('project_memory', {'result_id': result_id, 'review_id': review_id, 'applicability': applicability,
            'authoritative': False}, project_id)

    def list(self, project_id, mode='SOC_SINGLE'):
        self.repo.get('project', project_id)
        return [] if mode == 'EVALUATION' else self.repo.list('project_memory', project_id)
