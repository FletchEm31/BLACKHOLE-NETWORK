#!/usr/bin/env python3
"""
Boundary-case regression test for exit_audit_logger._determine_outcome().

Exists because of a real bug (fixed 2026-07-18): the 2026-07-07 fix made
"between" buckets correctly inclusive on both ends, then generalized that
to threshold ("or above"/"or below") buckets too, without evidence --
wrong. Threshold buckets use a STRICT Kalshi rule (>strike / <strike), so
an exact-boundary reading is a NO_WIN, not a NO_LOSS. Two real historical
trades (KMIA T94 2026-07-05, KMIA T97 2026-07-16) were misscored by this
before the fix.

Bucket definition this test enforces (see _determine_outcome()'s own
docstring for the full statement): every whole-degree reading belongs to
EXACTLY ONE bucket, boundaries touch but never overlap. Run this before
and after any change to _determine_outcome() -- if it doesn't pass
cleanly, don't deploy.

Usage:
    python3 test_determine_outcome_boundaries.py
"""
import sys
from exit_audit_logger import _determine_outcome

# (actual_tmax_f, bucket_floor, bucket_cap, expected_outcome, description)
CASES = [
    # ---- The exact four boundary assertions from the bucket-definition
    #      spec: "79-80" / "81 or above" and "72 or below" / "73-74". ----
    (80.0, 79.0, 80.0, 'NO_LOSS', 'temp=80 -> belongs to 79-80 (YES wins), never 81-or-above'),
    (81.0, 80.0, None, 'NO_LOSS', 'temp=81 -> belongs to 81-or-above (YES wins), never 79-80'),
    (72.0, None, 73.0, 'NO_LOSS', 'temp=72 -> belongs to 72-or-below (YES wins), never 73-74'),
    (73.0, 73.0, 74.0, 'NO_LOSS', 'temp=73 -> belongs to 73-74 (YES wins), never 72-or-below'),
    # The same four readings against the *other* bucket in each pair --
    # confirms exclusivity (NO wins the bucket the reading does NOT belong to).
    (80.0, 80.0, None, 'NO_WIN',  'temp=80 -> excluded from 81-or-above (NO wins)'),
    (81.0, 79.0, 80.0, 'NO_WIN',  'temp=81 -> excluded from 79-80 (NO wins)'),
    (72.0, 73.0, 74.0, 'NO_WIN',  'temp=72 -> excluded from 73-74 (NO wins)'),
    (73.0, None, 73.0, 'NO_WIN',  'temp=73 -> excluded from 72-or-below (NO wins)'),

    # ---- Standard "between" bucket, full shape (unchanged 2026-07-07 behavior) ----
    (90.0, 90.0, 91.0, 'NO_LOSS', 'between: actual==floor (inclusive)'),
    (91.0, 90.0, 91.0, 'NO_LOSS', 'between: actual==cap (inclusive)'),
    (89.9, 90.0, 91.0, 'NO_WIN',  'between: actual<floor'),
    (91.1, 90.0, 91.0, 'NO_WIN',  'between: actual>cap'),

    # ---- T-high threshold ("or above"), full shape ----
    (97.0, 97.0, None, 'NO_WIN',  'T-high: actual==floor exactly -- exact boundary is NO_WIN'),
    (98.0, 97.0, None, 'NO_LOSS', 'T-high: actual>floor -- YES wins'),
    (96.0, 97.0, None, 'NO_WIN',  'T-high: actual<floor'),

    # ---- T-low threshold ("or below"), full shape ----
    (90.0, None, 90.0, 'NO_WIN',  'T-low: actual==cap exactly -- exact boundary is NO_WIN'),
    (89.0, None, 90.0, 'NO_LOSS', 'T-low: actual<cap -- YES wins'),
    (91.0, None, 90.0, 'NO_WIN',  'T-low: actual>cap'),

    # ---- The two real historical rows this bug affected ----
    (94.0, 94.0, None, 'NO_WIN',  'row 6937 (KMIA T94, 2026-07-05): actual==94.0 exactly'),
    (97.0, 97.0, None, 'NO_WIN',  'row 12459 (KMIA T97, 2026-07-16): actual==97.0 exactly'),
]


def main() -> int:
    all_pass = True
    for actual, floor, cap, expected, desc in CASES:
        result = _determine_outcome(actual, floor, cap)
        ok = result == expected
        all_pass &= ok
        print(f"{'PASS' if ok else 'FAIL'}  {desc:70} got={result:8} expected={expected}")

    print("\nALL PASS" if all_pass else "\n*** FAILURES ABOVE -- DO NOT DEPLOY ***")
    return 0 if all_pass else 1


if __name__ == '__main__':
    sys.exit(main())
