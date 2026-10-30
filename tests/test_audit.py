import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import missing_eval_audit as a


def records(*rows):
    rows = [a.validate_record(r) for r in rows]
    return {(r['case_id'], r['trial']): r for r in rows}


def row(case='one', trial=0, **kw):
    return {'case_id': case, 'trial': trial, **kw}


class ReaderTests(unittest.TestCase):
    def read(self, text, limit=10000):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'data.jsonl'
            p.write_bytes(text if isinstance(text, bytes) else text.encode())
            return a.read_records(p, limit)

    def test_valid_unicode_and_optional_unknown(self):
        r = self.read('{"case_id":"中文","trial":0}\n')
        self.assertIsNone(r[('中文', 0)]['passed'])
        self.assertIsNone(r[('中文', 0)]['score'])

    def test_blank_line_rejected(self):
        with self.assertRaisesRegex(a.InputError, ':1: blank'):
            self.read('\n')

    def test_empty_file_valid(self):
        self.assertEqual(self.read(''), {})

    def test_duplicate_pair(self):
        with self.assertRaisesRegex(a.InputError, ':2: duplicate'):
            self.read('{"case_id":"a","trial":0}\n' * 2)

    def test_duplicate_json_field(self):
        with self.assertRaisesRegex(a.InputError, 'duplicate JSON field'):
            self.read('{"case_id":"a","case_id":"b","trial":0}')

    def test_nonfinite_literals(self):
        for x in ['NaN', 'Infinity', '-Infinity', '1e999']:
            with self.subTest(x=x), self.assertRaises(a.InputError):
                self.read('{"case_id":"a","trial":0,"score":' + x + '}')

    def test_invalid_utf8_line_location(self):
        with self.assertRaisesRegex(a.InputError, ':2:'):
            self.read(b'{"case_id":"a","trial":0}\n\xff')

    def test_json_error_location(self):
        with self.assertRaisesRegex(a.InputError, ':1:'):
            self.read('{')

    def test_record_limit(self):
        with self.assertRaisesRegex(a.InputError, 'record limit 1'):
            self.read('{"case_id":"a","trial":0}\n{"case_id":"b","trial":0}', 1)

    def test_byte_limit(self):
        with self.assertRaisesRegex(a.InputError, 'line exceeds'):
            self.read(' ' * (a.MAX_LINE_BYTES + 1))

    def test_total_byte_limit(self):
        from unittest.mock import patch
        with patch.object(a, 'MAX_INPUT_BYTES', 40):
            with self.assertRaisesRegex(a.InputError, 'input exceeds 40 bytes'):
                self.read('{"case_id":"a","trial":0}\n{"case_id":"b","trial":0}')

    def test_case_id_schema(self):
        for x in ['', '  ', 0, False, None, []]:
            with self.subTest(x=x), self.assertRaises(a.InputError):
                a.validate_record(row(case=x))

    def test_trial_schema(self):
        for x in [-1, 0.0, True, None, '0']:
            with self.subTest(x=x), self.assertRaises(a.InputError):
                a.validate_record(row(trial=x))

    def test_passed_schema(self):
        for x in [0, 1, 'true', [], {}]:
            with self.subTest(x=x), self.assertRaises(a.InputError):
                a.validate_record(row(passed=x))

    def test_score_schema(self):
        for x in [-.1, 1.1, True, '0.5', float('nan'), float('inf')]:
            with self.subTest(x=x), self.assertRaises(a.InputError):
                a.validate_record(row(score=x))
        for x in [0, 1, .5, None]:
            self.assertEqual(a.validate_record(row(score=x))['score'], x)

    def test_task_and_extra_fields(self):
        with self.assertRaises(a.InputError):
            a.validate_record(row(task=None))
        with self.assertRaises(a.InputError):
            a.validate_record(row(extra=1))
        with self.assertRaises(a.InputError):
            a.validate_record([])


def plan(*ids):
    return {(case, 0): {'case_id': case, 'trial': 0} for case in ids}


class AuditTests(unittest.TestCase):
    def test_empty_plan_rejected(self):
        with self.assertRaises(a.InputError):
            a.audit({}, {}, {})

    def test_both_absent_count_in_denominator(self):
        r = a.audit(plan('a', 'b', 'c'), records(row('a', passed=False)), records(row('a', passed=True)))
        b = r['binary']['planned_set_bounds']
        self.assertEqual((b['lower_numerator'], b['upper_numerator'], b['denominator']), (-1, 3, 3))
        self.assertEqual(r['counts']['both_missing'], 2)
        self.assertEqual(r['binary']['complete_case']['mean_difference'], 1)

    def test_example_selection_reversal(self):
        p = plan(*map(str, range(10)))
        l = records(row('0', passed=False), row('1', passed=False))
        r = records(row('0', passed=True), row('1', passed=True))
        x = a.audit(p, l, r)
        self.assertEqual(x['binary']['complete_case']['mean_difference'], 1)
        self.assertEqual(x['binary']['planned_set_bounds']['lower'], -.6)
        self.assertEqual(x['binary']['planned_set_bounds']['upper'], 1)

    def test_one_sided_known_constraints(self):
        x = a.audit(plan('a'), records(row('a', passed=True, score=.7)), {})
        self.assertEqual(x['binary']['planned_set_bounds']['lower'], -1)
        self.assertEqual(x['binary']['planned_set_bounds']['upper'], 0)
        self.assertAlmostEqual(x['score']['planned_set_bounds']['lower'], -.7)
        self.assertAlmostEqual(x['score']['planned_set_bounds']['upper'], .3)
        self.assertEqual(x['counts']['baseline_only'], 1)

    def test_present_null_distinguished_from_missing(self):
        x = a.audit(plan('a'), records(row('a', passed=None)), {})
        rec = x['binary']['records'][0]
        self.assertEqual((rec['baseline_state'], rec['candidate_state']), ('unknown', 'missing'))
        self.assertEqual(x['binary']['baseline_unknown'][0]['reason'], 'unknown_outcome')
        self.assertEqual(x['binary']['candidate_unknown'][0]['reason'], 'missing_record')

    def test_missing_field_unknown(self):
        x = a.audit(plan('a'), records(row('a')), records(row('a', passed=False)))
        self.assertEqual(x['binary']['records'][0]['baseline_state'], 'unknown')
        self.assertEqual(x['binary']['planned_set_bounds']['upper'], 0)

    def test_all_known_bounds_collapse(self):
        x = a.audit(plan('a', 'b'), records(row('a', passed=True, score=.4), row('b', passed=False, score=.3)), records(row('a', passed=False, score=.2), row('b', passed=True, score=.9)))
        self.assertEqual(x['binary']['planned_set_bounds']['lower'], 0)
        self.assertEqual(x['binary']['planned_set_bounds']['upper'], 0)
        self.assertAlmostEqual(x['score']['planned_set_bounds']['lower'], .2)
        self.assertEqual(x['score']['planned_set_bounds']['lower'], x['score']['planned_set_bounds']['upper'])

    def test_all_missing_full_bounds(self):
        x = a.audit(plan('a', 'b'), {}, {})
        for name in ['binary', 'score']:
            self.assertEqual(x[name]['planned_set_bounds']['lower'], -1)
            self.assertEqual(x[name]['planned_set_bounds']['upper'], 1)
            self.assertIsNone(x[name]['complete_case']['mean_difference'])

    def test_outcome_subsets_independent(self):
        x = a.audit(plan('a'), records(row('a', score=.2)), records(row('a', passed=True, score=.3)))
        self.assertEqual(x['binary']['complete_case']['pairs'], 0)
        self.assertEqual(x['score']['complete_case']['pairs'], 1)
        self.assertEqual(x['binary']['baseline_coverage'], 0)
        self.assertEqual(x['score']['baseline_coverage'], 1)

    def test_unplanned_rejected_each_side(self):
        for left, right in [(records(row('x')), {}), ({}, records(row('x')))]:
            with self.assertRaisesRegex(a.InputError, 'unplanned'):
                a.audit(plan('a'), left, right)

    def test_task_label_and_presence_mismatch(self):
        p = records(row('a', task='task'))
        for r in [row('a'), row('a', task='other')]:
            with self.assertRaisesRegex(a.InputError, 'task mismatch'):
                a.audit(p, records(r), {})

    def test_repeated_trial_records_allowed(self):
        p = records(row('a', trial=0), row('a', trial=1))
        x = a.audit(p, records(row('a', trial=0, passed=False)), records(row('a', trial=0, passed=True)))
        self.assertEqual(x['counts']['planned'], 2)
        self.assertEqual(x['binary']['planned_set_bounds']['lower'], 0)

    def test_strict_plan_schema(self):
        with self.assertRaisesRegex(a.InputError, 'unknown fields'):
            a.validate_record(row(passed=True), plan=True)
        self.assertEqual(a.validate_record(row(), plan=True), row())

    def test_directions(self):
        for left, right, direction in [(False, True, 'candidate_better'), (True, False, 'baseline_better'), (True, True, 'not_identified')]:
            x = a.audit(plan('a'), records(row('a', passed=left)), records(row('a', passed=right)))
            self.assertEqual(x['binary']['planned_set_bounds']['direction'], direction)

    def test_score_direction_tolerance(self):
        x = a.audit(plan('a'), records(row('a', score=.3)), records(row('a', score=.30000000000000004)))
        b = x['score']['planned_set_bounds']
        self.assertEqual(b['direction'], 'not_identified_numerical_tolerance')
        self.assertTrue(b['contains_zero_with_tolerance'])
        self.assertEqual(b['direction_tolerance'], 1e-12)

    def test_order_independence(self):
        p = plan('b', 'a'); l = records(row('b', score=.5), row('a', score=.2)); r = records(row('a', score=.3))
        self.assertEqual(a.audit(p, l, r), a.audit(dict(reversed(list(p.items()))), dict(reversed(list(l.items()))), r))

    def test_markdown_untrusted_fences(self):
        x = a.audit(plan('````\n<script>'), {}, {})
        md = a.markdown(x)
        self.assertIn('\\u0060', md)
        self.assertEqual(md.count('````'), 14)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.p = self.root / 'plan.jsonl'; self.l = self.root / 'baseline.jsonl'; self.r = self.root / 'candidate.jsonl'
        self.p.write_text(json.dumps(row()) + '\n'); self.l.write_text(json.dumps(row(passed=False)) + '\n'); self.r.write_text(json.dumps(row(passed=True)) + '\n')

    def cli(self, *args):
        return subprocess.run([sys.executable, a.__file__, str(self.p), str(self.l), str(self.r), *map(str, args)], capture_output=True, text=True)

    def test_json_stdout(self):
        r = self.cli();self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)['binary']['planned_set_bounds']['lower_numerator'], 1)

    def test_markdown_file(self):
        out = self.root / 'report.md';r = self.cli('--format', 'markdown', '--output', out)
        self.assertEqual(r.returncode, 0, r.stderr);self.assertEqual(r.stdout, '')
        self.assertIn('not confidence intervals', out.read_text())

    def test_existing_output_preserved(self):
        out = self.root / 'report.json';out.write_text('keep')
        self.assertEqual(self.cli('--output', out).returncode, 2);self.assertEqual(out.read_text(), 'keep')

    def test_inputs_preserved(self):
        for path in [self.p, self.l, self.r]:
            before = path.read_bytes();self.assertEqual(self.cli('--output', path).returncode, 2);self.assertEqual(before, path.read_bytes())

    def test_error_localized_no_stdout(self):
        self.r.write_text('bad');r = self.cli()
        self.assertEqual(r.returncode, 2);self.assertIn('candidate.jsonl:1:', r.stderr);self.assertEqual(r.stdout, '')

    def test_limit_argument(self):
        r = self.cli('--max-records', 0);self.assertEqual(r.returncode, 2);self.assertEqual(r.stdout, '')

    def test_plan_disallows_results(self):
        self.p.write_text(json.dumps(row(passed=True)));r = self.cli()
        self.assertEqual(r.returncode, 2);self.assertIn('plan.jsonl:1:', r.stderr)


if __name__ == '__main__':
    unittest.main()
