"""Render the stored manifest for a draft pull request."""


def draft_summary(manifest):
    candidate = manifest['candidate']
    references = {(criterion, source): reference for criterion, source, reference in manifest.get('references', ())}

    def result(criterion, source, status):
        reference = references.get((criterion, source))
        if reference is not None:
            status = f'[{status}](https://github.com/{candidate["repository"]}/{reference})'
        return f'`{source}` {status}'

    lines = [
        '## Task verification', '',
        f'{candidate["issue"]} · Run `{manifest["run_id"]}` · Attempt {manifest["attempt"]}',
        f'Commit `{candidate["commit"]}` · Decision **{manifest["decision"]}**',
        '',
        '### Required checks', '',
    ]
    lines.extend(f'- {result(None, source, status)}' for source, status in manifest['checks'])
    lines.extend(('', '### Acceptance criteria', ''))
    for criterion, sources in manifest['criteria']:
        details = ', '.join(result(criterion, source, status) for source, status in sources)
        lines.append(f'- `{criterion}` · {details}')
    if manifest['stop_reason'] is not None:
        lines.extend(('', f'Stopped · `{manifest["stop_reason"]}`'))
    return '\n'.join(lines) + '\n'
