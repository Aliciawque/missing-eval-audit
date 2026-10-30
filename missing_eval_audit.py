#!/usr/bin/env python3
"""Audit missing outcomes against a fixed plan without treating them as failures."""
import argparse
import json
import math
from pathlib import Path
import sys

MAX_RECORDS = 10_000
MAX_LINE_BYTES = 1_048_576
MAX_INPUT_BYTES = 16 * 1_048_576
ALLOWED_FIELDS = {'case_id', 'trial', 'passed', 'score', 'task'}


class InputError(ValueError):
    """Invalid or incompatible evaluation data."""


def _object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise InputError(f'duplicate JSON field {key!r}')
        obj[key] = value
    return obj


def _constant(value):
    raise InputError(f'non-finite JSON constant {value}')


def validate_record(record, plan=False):
    if not isinstance(record, dict):
        raise InputError('record must be an object')
    extra = set(record) - ({'case_id', 'trial', 'task'} if plan else ALLOWED_FIELDS)
    if extra:
        raise InputError('unknown fields: ' + ', '.join(sorted(extra)))
    case = record.get('case_id')
    if not isinstance(case, str) or not case.strip():
        raise InputError('case_id must be a non-empty string')
    trial = record.get('trial')
    if type(trial) is not int or trial < 0:
        raise InputError('trial must be an integer >= 0')
    passed = record.get('passed')
    if passed is not None and type(passed) is not bool:
        raise InputError('passed must be boolean or null')
    score = record.get('score')
    if score is not None and (type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1):
        raise InputError('score must be a finite number in [0, 1] or null')
    if 'task' in record and not isinstance(record['task'], str):
        raise InputError('task must be a string when present')
    return {'case_id': case, 'trial': trial,
            **({} if plan else {'passed': passed, 'score': score}),
            **({'task': record['task']} if 'task' in record else {})}


def read_records(path, max_records=MAX_RECORDS, plan=False):
    if not 1 <= max_records <= MAX_RECORDS:
        raise InputError(f'max_records must be between 1 and {MAX_RECORDS}')
    result = {}
    path = Path(path)
    try:
        with path.open('rb') as handle:
            line_number = 0
            total_bytes = 0
            while True:
                raw = handle.readline(MAX_LINE_BYTES + 1)
                if not raw:
                    break
                line_number += 1
                prefix = f'{path}:{line_number}'
                total_bytes += len(raw)
                if total_bytes > MAX_INPUT_BYTES:
                    raise InputError(f'{prefix}: input exceeds {MAX_INPUT_BYTES} bytes')
                if len(raw) > MAX_LINE_BYTES:
                    raise InputError(f'{prefix}: line exceeds {MAX_LINE_BYTES} bytes')
                try:
                    line = raw.decode('utf-8')
                    if not line.strip():
                        raise InputError('blank lines are not records')
                    record = validate_record(json.loads(line, object_pairs_hook=_object, parse_constant=_constant), plan=plan)
                except (ValueError, UnicodeError, OverflowError, RecursionError) as exc:
                    raise InputError(f'{prefix}: {exc}') from exc
                key = (record['case_id'], record['trial'])
                if key in result:
                    raise InputError(f'{prefix}: duplicate (case_id, trial) {key!r}')
                if len(result) >= max_records:
                    raise InputError(f'{prefix}: record limit {max_records} exceeded')
                result[key] = record
    except OSError as exc:
        raise InputError(f'{path}: {exc}') from exc
    return result


def _key(key):
    return {'case_id': key[0], 'trial': key[1]}


def _outcome(plan, baseline, candidate, field):
    n = len(plan)
    lower = []
    upper = []
    complete = []
    baseline_known = candidate_known = 0
    baseline_unknown = []
    candidate_unknown = []
    per_record = []
    for key in sorted(plan):
        left, right = baseline.get(key), candidate.get(key)
        lv = left.get(field) if left is not None else None
        rv = right.get(field) if right is not None else None
        if lv is None:
            baseline_unknown.append({**_key(key), 'reason': 'missing_record' if left is None else 'unknown_outcome'})
        else:
            baseline_known += 1
        if rv is None:
            candidate_unknown.append({**_key(key), 'reason': 'missing_record' if right is None else 'unknown_outcome'})
        else:
            candidate_known += 1
        lmin, lmax = (0, 1) if lv is None else (int(lv), int(lv)) if field == 'passed' else (lv, lv)
        rmin, rmax = (0, 1) if rv is None else (int(rv), int(rv)) if field == 'passed' else (rv, rv)
        per_record.append({**_key(key),
                           'baseline_state': 'missing' if left is None else 'unknown' if lv is None else 'known',
                           'candidate_state': 'missing' if right is None else 'unknown' if rv is None else 'known',
                           'baseline_value': lv, 'candidate_value': rv,
                           'difference_lower': rmin - lmax, 'difference_upper': rmax - lmin})
        lower.append(rmin - lmax)
        upper.append(rmax - lmin)
        if lv is not None and rv is not None:
            complete.append(int(rv) - int(lv) if field == 'passed' else rv - lv)
    low_sum = sum(lower) if field == 'passed' else math.fsum(lower)
    high_sum = sum(upper) if field == 'passed' else math.fsum(upper)
    complete_sum = sum(complete) if field == 'passed' else math.fsum(complete)
    bounds = {'lower': low_sum / n, 'upper': high_sum / n,
              'contains_zero': low_sum <= 0 <= high_sum,
              'direction': 'candidate_better' if low_sum > 0 else 'baseline_better' if high_sum < 0 else 'not_identified'}
    if field != 'passed':
        tolerance = 1e-12
        bounds['direction_tolerance'] = tolerance
        bounds['contains_zero_with_tolerance'] = bounds['lower'] <= tolerance and bounds['upper'] >= -tolerance
        bounds['direction'] = ('candidate_better' if bounds['lower'] > tolerance else
                               'baseline_better' if bounds['upper'] < -tolerance else
                               'not_identified' if bounds['contains_zero'] else
                               'not_identified_numerical_tolerance')
    if field == 'passed':
        bounds.update({'lower_numerator': low_sum, 'upper_numerator': high_sum, 'denominator': n})
    cc = {'pairs': len(complete), 'mean_difference': complete_sum / len(complete) if complete else None}
    if field == 'passed':
        cc.update({'numerator': complete_sum if complete else None, 'denominator': len(complete)})
    return {'baseline_known': baseline_known, 'candidate_known': candidate_known,
            'baseline_coverage': baseline_known / n, 'candidate_coverage': candidate_known / n,
            'baseline_unknown': baseline_unknown, 'candidate_unknown': candidate_unknown,
            'complete_case': cc, 'planned_set_bounds': bounds, 'records': per_record}


def audit(plan, baseline, candidate):
    """Audit validated records keyed by (case_id, trial); use read_records first."""
    if not plan:
        raise InputError('plan must contain at least one record')
    for label, result in [('baseline', baseline), ('candidate', candidate)]:
        unplanned = result.keys() - plan.keys()
        if unplanned:
            raise InputError(f'{label}: unplanned result {sorted(unplanned)[0]!r}')
        for key, record in result.items():
            if record.get('task') != plan[key].get('task'):
                raise InputError(f'{label}: task mismatch for {key!r}; task must match plan including presence')
    both_missing = sorted(plan.keys() - baseline.keys() - candidate.keys())
    baseline_only = sorted(baseline.keys() - candidate.keys())
    candidate_only = sorted(candidate.keys() - baseline.keys())
    return {
        'schema_version': 1,
        'direction': 'candidate minus baseline; higher scores and passed=true are better',
        'counts': {'planned': len(plan), 'baseline_records': len(baseline), 'candidate_records': len(candidate),
                   'both_present': len(baseline.keys() & candidate.keys()), 'both_missing': len(both_missing),
                   'baseline_only': len(baseline_only), 'candidate_only': len(candidate_only)},
        'both_missing': [_key(k) for k in both_missing],
        'baseline_only': [_key(k) for k in baseline_only],
        'candidate_only': [_key(k) for k in candidate_only],
        'binary': _outcome(plan, baseline, candidate, 'passed'),
        'score': _outcome(plan, baseline, candidate, 'score'),
        'limitations': [
            'Bounds describe only the fixed planned records; they are identification bounds, not confidence intervals or significance tests.',
            'Every absent or unknown outcome can independently take any value from 0 to 1; no missing-at-random assumption is made.',
            'Repeated trials are allowed and each planned record receives equal weight; cases with more planned trials receive more total weight.',
            'Complete-case differences describe a selected subset and may reverse when missing outcomes are included.',
            'Binary and score coverage are separate; an observed score does not determine a missing pass/fail result.',
            'Unique keys and matching task labels do not establish that measurements or the plan are valid or pre-registered.',
            'Binary bounds retain exact integer numerators; score bounds use floating-point arithmetic and a 1e-12 direction tolerance.',
            'No generalization to unplanned cases is justified; this tool does not execute evaluations.'
        ]}


def markdown(report):
    c = report['counts']
    lines = ['# Missing evaluation audit', '', report['direction'], '',
             f"Planned: {c['planned']}; baseline records: {c['baseline_records']}; candidate records: {c['candidate_records']}; both absent: {c['both_missing']}.", '',
             '**These are fixed-plan identification bounds, not confidence intervals or significance tests.**', '']
    for name in ['binary', 'score']:
        o = report[name]; b = o['planned_set_bounds']; cc = o['complete_case']
        lines.extend([f'## {name.title()} outcomes', '',
                      f"Known baseline: {o['baseline_known']}/{c['planned']}; known candidate: {o['candidate_known']}/{c['planned']}.",
                      f"Complete-case difference: {cc['mean_difference']} from {cc['pairs']} pairs.",
                      f"Planned-set difference bounds: [{b['lower']}, {b['upper']}]; contains zero: {b['contains_zero']}; direction: {b['direction']}."])
        if name == 'binary':
            lines.append(f"Exact bounds: [{b['lower_numerator']}/{b['denominator']}, {b['upper_numerator']}/{b['denominator']}].")
        else:
            lines.append(f"Score direction tolerance: {b['direction_tolerance']}; zero included with tolerance: {b['contains_zero_with_tolerance']}.")
        lines.append('')
    lines.extend(['## Missing and unknown evidence', ''])
    sections = [('Both missing', report['both_missing']), ('Baseline only', report['baseline_only']), ('Candidate only', report['candidate_only'])]
    for name in ['binary', 'score']:
        for side in ['baseline', 'candidate']:
            sections.append((f'{name} {side} unknown', report[name][side + '_unknown']))
    for title, records in sections:
        lines.extend([f'### {title}', '', '````json', json.dumps(records, ensure_ascii=True, indent=2).replace('`', '\\u0060'), '````', ''])
    lines.extend(['## Interpretation limits', ''] + ['- ' + item for item in report['limitations']])
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description='Bound fixed-plan evaluation differences without calling missing outcomes failures.')
    parser.add_argument('plan', type=Path)
    parser.add_argument('baseline', type=Path)
    parser.add_argument('candidate', type=Path)
    parser.add_argument('--format', choices=['json', 'markdown'], default='json')
    parser.add_argument('--output', type=Path, help='create a report instead of stdout; refuses existing files')
    parser.add_argument('--max-records', type=int, default=MAX_RECORDS, help='per input, 1..10000')
    args = parser.parse_args(argv)
    try:
        if args.output and args.output.resolve() in {args.plan.resolve(), args.baseline.resolve(), args.candidate.resolve()}:
            raise InputError('output must not overwrite an input')
        result = audit(read_records(args.plan, args.max_records, plan=True), read_records(args.baseline, args.max_records), read_records(args.candidate, args.max_records))
        text = markdown(result) if args.format == 'markdown' else json.dumps(result, indent=2, ensure_ascii=True, allow_nan=False) + '\n'
        if args.output:
            with args.output.open('x', encoding='utf-8') as handle:
                handle.write(text)
        else:
            sys.stdout.write(text)
    except (InputError, ValueError, OSError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
