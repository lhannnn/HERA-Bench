"""Matched-pair aggregation with explicit incomplete-run handling."""
import json
from pathlib import Path


def summarize(directory):
    root = Path(directory)
    meta = json.loads((root / 'run.json').read_text())
    pairs = meta['pairs']
    if (not pairs or len(set(pairs)) != len(pairs)
            or any(type(n) is not int or not 1 <= n <= 60 for n in pairs)):
        raise ValueError('Invalid selected pair IDs')
    expected = {(n, side) for n in pairs for side in ('act', 'abstain')}
    rows = {}
    for path in sorted(root.glob('hera_*/*/result.json')):
        row = json.loads(path.read_text())
        key = (row['number'], row['side'])
        if key not in expected or key in rows or row['pair_id'] != f"hera_{row['number']:03}":
            raise ValueError('Unexpected, duplicate, or inconsistent episode ID')
        if path.relative_to(root).as_posix() != f"{row['pair_id']}/{row['side']}/result.json":
            raise ValueError('Episode path does not match its ID')
        if row['harness'] != meta['harness']:
            raise ValueError('Mixed harness results')
        if type(row['passed']) is not bool or type(row['runtime_valid']) is not bool:
            raise ValueError('Episode verdicts must be booleans')
        if row['passed'] and not row['runtime_valid']:
            raise ValueError('A runtime failure cannot be counted as correct')
        rows[key] = row
    complete = set(rows) == expected
    ok = lambda n, s: rows.get((n, s), {}).get('passed', False)
    numerators = {'Act': sum(ok(n, 'act') for n in pairs),
                  'Abstain': sum(ok(n, 'abstain') for n in pairs),
                  'Pair': sum(ok(n, 'act') and ok(n, 'abstain') for n in pairs)}
    return {
        'scope': 'full-benchmark' if set(pairs) == set(range(1, 61)) else 'subset',
        'complete': complete, 'selected_pairs': len(pairs), 'expected_episodes': len(expected),
        'recorded_episodes': len(rows), 'runtime_failures': sum(not r['runtime_valid'] for r in rows.values()),
        'missing': [f'hera_{n:03}/{s}' for n, s in sorted(expected-set(rows))],
        'correct_counts': numerators,
        'percentages': {k: 100*v/len(pairs) for k, v in numerators.items()} if complete else None,
        'completed_model_usage': {k: sum((r.get('usage') or {}).get(k, 0) for r in rows.values())
                                  for k in ('input_tokens', 'output_tokens', 'requests')},
        'unresolved_model_calls': sum(r.get('unresolved_model_calls', 0) for r in rows.values()),
        'dataset_revision': meta['dataset']['revision'], 'harness': meta['harness'],
        'code_sha256': meta['code_sha256'],
    }
