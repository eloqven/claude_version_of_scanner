# Fix V2 Windows Unicode Crash + Compile Docs

## Summary

- Fix confirmed V2 crash when stdout is Windows cp1252 and Binance returns non-ASCII symbols.
- Fix the documented compile command that fails in PowerShell.
- Keep scope minimal: no V2 status-contract change, no scan-progress rewrite.

## Key Changes

- In `binance_scanner_v2.py`, add the same `_setup_console()` pattern already used by V1/proto:
  - reconfigure `sys.stdout` and `sys.stderr` to `encoding="utf-8", errors="replace"`;
  - call it at import time before scanner output;
  - keep log-file writes as UTF-8.
- In `README.md` and `help.md`, replace the wildcard compile command with PowerShell-safe commands:
  - `python -m py_compile ...top-level scripts...`
  - `python -m compileall -q scanner_v2`

## Tests

- Add a V2 cp1252 regression test proving non-ASCII warning/log output does not raise `UnicodeEncodeError`.
- Run:
  - `python -m py_compile scanner_common.py binance_scanner_v1.py binance_scanner_proto.py binance_scanner_v2.py log_dashboard.py`
  - `python -m compileall -q scanner_v2`
  - `python -m unittest discover -s tests`
  - `python binance_scanner_v2.py --max-scan 3`

## Assumptions

- Do not change V2 payload statuses yet; rejected data still reports as today.
- Do not create new docs beyond this requested plan file; only edit existing README/help.
- Do not commit logs, DBs, or unrelated untracked files.
