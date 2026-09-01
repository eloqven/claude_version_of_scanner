# Document Review — Planning & Requirements (feat/adaptive-tp-scanner-v2)

Scope: planning/requirements docs on the branch — `fib_matrix_v3_plan.md`
(primary), `scanner_v2_plan.md`, `v2_unicode_fix_plan.md`,
`binance_scanner_fixes_plan.md` — plus the newly added `README.md` / `help.md`
sections for the 1s scraper and V3.

Reviewer lenses: coherence, feasibility, completeness, accuracy.
No cross-model peer route configured; single-pass local review.

## Verdict: REQUIRES REVISION (one coherence break + doc/impl divergence)

The plans are well-structured and mostly match the code surface. The blocking
issue is that the headline plan (`fib_matrix_v3_plan.md`) describes a pipeline
the implementation does not currently deliver, and the docs I added in the
prior step describe that pipeline as working.

## COHERENCE — plan implies resampling the implementation lacks

`fib_matrix_v3_plan.md`:
- "Evaluate on a 1-minute cadence using the latest closed value from each
  matrix element"
- Test plan: "deterministic 1s fixture resamples to all fib intervals"
- Matrix definition lists intervals `5m, 8m, 13m, 21m, 34m, 55m` while the data
  source is `1s`.

These only make sense if the engine fetches `1s` and resamples to each fib
interval. `FibMatrix.build_matrix` instead queries the archive **directly** at
each fib interval, which `ArchiveCandleSource` rejects. So the documented
design and the built design diverge — the plan is not satisfied by the code.

**Action:** either (a) fix `build_matrix` to fetch `1s` + `resample_candles`
per interval (recommended; matches the plan and tests), or (b) revise the plan
and tests to describe the actual (broken) direct-query approach. (a) is correct.

## ACCURACY — README/help overstate current V3 behavior

The `README.md` and `help.md` sections added in the previous commit state that
V3 "builds Fibonacci interval × period MA matrices … and records reaction
events" and "Emits … `V3_EVENT` / `V3_SUMMARY` JSON lines." Today the engine
emits **zero** events (see code-review CRITICAL). Until the resample fix lands,
these docs describe behavior the code does not produce.

**Action:** add a one-line caveat ("currently being wired; end-to-end event
emission pending the source/resample fix") or hold the doc merge until the fix
is in. The `Caveat` should be removed once `build_matrix` is verified against a
real 1s source.

## COMPLETENESS — plan items not yet evidenced

`fib_matrix_v3_plan.md` "Test Plan" lists V3 tests:
- "EMA/WMA/SMA matrix values match expected fixture calculations" — covered
  only at the `_compute_ma` level, never through `build_matrix` with resampling.
- "CLI emits `V3_EVENT` and persists event rows" — there is no test asserting a
  `V3EventStore` row is written after a real analysis path.

The plan is otherwise complete (URL construction, checksum, CSV parse,
gap/duplicate, skip-already-valid, clustering, classification, reaction
metrics all have unit coverage).

## FEASIBILITY — verification commands under-specified

The plan's "Verification commands" are compile + `unittest discover` only. None
exercise an end-to-end `binance_scanner_v3.py` run (understandable — needs
archive data), but the absence of even a mocked end-to-end `build_matrix →
detect_events → record_event` test is what let the critical bug through. Add a
mocked end-to-end test to the plan's verification list.

## Consistent / correct

- 108-element matrix count: 6 intervals × 3 MA types × 6 periods — code
  (`FIB_INTERVALS`, `MA_TYPES`, `MA_PERIODS`) matches the plan exactly.
- CLI surface (`--symbols/--start/--end/--archive-dir/--archive-db/--event-db/
  --bootstrap-missing`) matches between plan, `binance_scanner_v3.py`, and the
  new help.md section.
- `.gitignore` (`data/binance_1s/`, `scanner_archive.db`, `fib_matrix_v3.db`)
  covers the plan's "ignore archive data and archive DB" requirement.
- "No dashboard route for V3 in this slice" assumption is honored.
