# Missing Eval Audit

A dependency-free Python CLI that asks: **could missing evaluation results reverse the apparent improvement?**

Supply a fixed plan and two JSONL result files. The tool includes planned cases missing from **both** runs, keeps missing records separate from present-but-unknown outcomes, and bounds the candidate-minus-baseline difference without inventing failed results.

It does not run models, grade answers, estimate statistical significance, or replace an evaluation framework. The bounds describe only the supplied plan—not future performance.

## Quick start

Python 3.10 or newer; standard library only. No installation, network access, account, or API key is required.

```sh
python3 missing_eval_audit.py examples/plan.jsonl examples/baseline.jsonl examples/candidate.jsonl
python3 missing_eval_audit.py examples/plan.jsonl examples/baseline.jsonl examples/candidate.jsonl --format markdown --output report.md
python3 -m unittest discover -s tests -v
```

The synthetic example has ten planned records, but only two completed pairs. Both are candidate wins:

- Complete-case binary improvement: `+1.0` (two pairs).
- Eight records are absent from both files.
- Full planned-set improvement bounds: `[-6/10, 10/10]`, or `[-0.6, 1.0]`.

The observed subset looks excellent, but the missing outcomes can reverse its direction. “Not identified” is not “no effect.”

## Input contract

Every file is UTF-8 JSON Lines: one object per line, no blank lines. An empty result file is valid; an empty plan is not. Fields not listed below are rejected, helping catch misspellings.

Plan row:

```json
{"case_id":"render-button","trial":0,"task":"ui"}
```

Result row:

```json
{"case_id":"render-button","trial":0,"task":"ui","passed":true,"score":0.8}
```

- `case_id`: required nonempty string; whitespace-only values are rejected. IDs are matched exactly, without trimming or case folding.
- `trial`: required integer greater than or equal to zero; booleans and floats are rejected.
- `task`: optional string. Its exact value **and presence** must match the plan for each result.
- `passed`: result-only boolean or `null`. Missing field and `null` both mean unknown, not false.
- `score`: result-only finite number between zero and one, or `null`. Missing field means unknown. Booleans, numeric strings, `NaN`, and infinities are rejected.

The `(case_id, trial)` key must be unique within each file. Duplicate JSON object fields are rejected too. Results outside the plan are errors, not silently dropped. File/line locations accompany parsing and schema errors; cross-file identity errors name the side and key.

Binary and score outcomes are independent: the tool never derives a missing pass/fail result from a score, or vice versa. A present record with both outcomes missing remains a present record with unknown outcomes.

## How the bounds work

Let the plan contain `N` equally weighted records. For each record, an observed outcome has interval `[value, value]`; an absent or unknown outcome has interval `[0, 1]`. For pass/fail, false is zero and true is one.

For baseline interval `[b_min, b_max]` and candidate interval `[c_min, c_max]`, that record's difference lies in:

```text
[c_min - b_max, c_max - b_min]
```

Summing the lower and upper endpoints separately and dividing by `N` gives fixed-plan worst/best bounds. Each unknown is allowed to vary independently. No missing-at-random assumption or probabilistic model is used.

Binary reports retain exact integer lower/upper numerators and the planned denominator. Scores use ordinary floating-point arithmetic. These are identification bounds—not confidence intervals, bootstrap intervals, p-values, or a new statistical method.

A strictly positive lower bound is labeled `candidate_better`; a strictly negative upper bound is `baseline_better`; an interval including zero is `not_identified`. For scores, direction classification uses a conservative absolute tolerance of `1e-12`; a near-zero interval is `not_identified_numerical_tolerance` when floating-point endpoints exclude zero by less than that tolerance. Raw endpoints and `contains_zero` are preserved, alongside `contains_zero_with_tolerance`. Binary classification uses exact integer arithmetic with no tolerance. This classification concerns this fixed plan under the stated outcome bounds only.

## Reports

JSON is written to stdout by default. `--format markdown` gives a human-readable report; `--output PATH` creates a new file and refuses to overwrite an existing file or any input. Exit status is `0` on success and `2` on input/output errors. Inputs are read-only.

The report includes:

- Planned, present, both-absent, and one-sided record counts and key lists.
- Binary and score known-outcome coverage, separately for each side.
- Complete-case differences with their actual denominators.
- Full planned-set lower/upper bounds and whether zero is included.
- Per-record `known`, `unknown`, or `missing` states, values, and difference endpoints in JSON.
- Explicit unknown lists with `missing_record` versus `unknown_outcome` reasons. The latter means null or an omitted outcome field in a present record.

Keys are sorted for deterministic reports. Markdown places user-supplied IDs in fenced JSON and escapes backticks. Reports contain case IDs and may therefore be sensitive; keep them local unless sharing is authorized.

## Limits and interpretation

- Defaults to at most 10,000 records **per input**; `--max-records` can lower that limit. Individual lines are limited to 1 MiB and each input file to 16 MiB.
- Repeated trials are allowed because there is no sampling inference. Each record has equal weight, so a case with more planned trials receives more total weight.
- The plan should be fixed before inspecting outcomes. The tool cannot verify pre-registration, measurement quality, task comparability, or selection bias in the plan itself.
- Complete-case estimates can be selection-biased. Bounds may be wide; this is information about missing evidence, not a reason to fill unknowns with zero.
- Outcomes must genuinely lie in `[0, 1]`; normalize other score scales explicitly before use.
- No cluster weighting, score transformations, model execution, hidden telemetry, or network calls are included.
- No inference is made about unplanned cases, future runs, or a wider population.

## Background and related tools

Public skill-evaluation workflows, including [Expo's skill evaluation tooling](https://github.com/expo/skills/tree/main/.claude/skills/expo-skill-eval), motivated keeping report completeness distinct from measured outcomes. This project is an independent implementation with a deliberately narrow interchange format; it does not claim native compatibility with that workflow.

Related projects [paired-eval](https://github.com/Yongjian-Wang6/paired-eval) and [evaldelta](https://github.com/brohum10/evaldelta) cover paired statistical comparison. This tool instead requires an explicit planned sample set and reports worst/best outcome bounds, including records absent from both result files. It does not claim those projects are deficient or reproduce their inferential features.

The general idea of bounding means with missing bounded outcomes is established, not invented here; see [Beresteanu and Manski (2000), section 6](https://faculty.wcas.northwestern.edu/cfm754/bounds_matlab.pdf). The equations above apply that elementary interval argument to a finite, equally weighted planned set. This is original Python code, not a port or copy of the cited implementation.

All included data is synthetic. Code and tests are original; no upstream implementation was copied. MIT license.
