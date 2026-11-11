from importlib.metadata import entry_points


def load_collectors():
    # Entry points are installed trusted Python packages, never config-supplied code.
    from goco.models.integrations.mail.mime import extract_candidates
    result={'gmail':extract_candidates}
    for entry in entry_points(group='goco.collectors'):
        if entry.name in result: raise ValueError('duplicate_collector_name')
        result[entry.name]=entry.load()
    return result
