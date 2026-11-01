def records(repo, kind, **filters):
    """Read every page, including history beyond Repository.list's page cap."""
    result, after = [], None
    while True:
        page = repo.list(kind, after=after, limit=1000, **filters)
        result.extend(page)
        if len(page) < 1000:
            return result
        after = page[-1]['id']
