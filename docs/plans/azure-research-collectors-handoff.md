# Agent Handoff: Azure Research Collectors

## Goal

Move the research-only workloads to the Azure VM and collect fresh data continuously:

- Scanner V1/V2/V3
- Meridian Hourly with RPR observation
- Forecast cycles: 16h, 24h, and 48h

Exclude Guard/live trading, testnet, credentials, Harbor, OpenClaw, and legacy Meridian.

## Verified Environment

- SSH: `ssh -i C:\Users\and_v\.ssh\id_rsa andrei@20.240.208.153`
- VM: `vm-opencode-247`, Ubuntu, Python 3.12, systemd, approximately 23 GB free.
- Keep the existing VM repositories and `agent-worker.service` untouched.
- Linkage reference: `D:\cloud-open-code\codex-cli-vm-install.md`
- Before changing Crypto-101, re-read the complete bodies and comments of GitHub issues #11, #18, and #24. Do not merge or interfere with the active recovery work.

## Implementation

### 1. Preserve and isolate the code

- Create `D:\claude_version_of_scanner-cloud-deploy`.
- Import the VM-only scanner commits `52258b4` and `0262fc2` from the existing VM checkout using a Git bundle or format-patch.
- Create and push `deploy/cloud-scanner-20260831`.
- Create `D:\crypto-101-cloud-research` from `origin/feature/meridian-rpr-observation-ui@6951f82`.
- Create and push `deploy/cloud-research-collectors-20260831`.
- On the VM, deploy fresh clones to:
  - `/home/andrei/agent/projects/claude-scanner-cloud`
  - `/home/andrei/agent/projects/crypto-101-cloud-research`
- Never overwrite the existing VM checkouts or their untracked files.

### 2. Apply only collector-reliability fixes

- Fix Meridian's `Cannot parse as Decimal: 'None'` failure by treating optional null numeric limits as disabled/omitted while preserving explicit zero values and provenance.
- Add a regression test proving a Meridian scan with a null maximum planned win records an integer `training_rows_recorded` value without a warning.
- Replace the scanner's truncating 48-hour runner behavior with append-only run receipts.
- Add V3 checkpointing keyed by symbol, UTC date, archive checksum, and collector version:
  - Skip an unchanged successful unit.
  - Retry unavailable or failed dates.
  - Record changed source/version processing as a separately labelled evaluation.
- Do not change prediction strategy, ML models, thresholds, candidate selection, or trading behavior.

### 3. Separate fresh data from historical evidence

Use:

- `/home/andrei/agent/data/research-collectors/current/scanner`
- `/home/andrei/agent/data/research-collectors/current/crypto-101`
- `/home/andrei/agent/data/research-collectors/archive/<UTC-timestamp>`

Copy the existing selected Windows histories and previous VM histories into dated, read-only archives. Include a manifest with relative path, size, modification time, and SHA-256. Use SQLite online backups for active databases.

Do not merge old data into the fresh canonical stores. Do not delete local or prior VM data. Backups remain VM-disk-only, so VM disk loss remains an accepted risk.

### 4. Install persistent VM services

Create separate virtual environments and derive dependencies from the actual project manifests and import checks.

Install systemd workloads running as `andrei`:

- `research-scanner-live`: hourly V1 then V2, sequential and locked.
- `research-scanner-v3`: daily check for unprocessed archive dates.
- `research-meridian`: hourly one-shot with RPR enabled; no server, browser, or Guard dispatch.
- `research-rpr-observer`: continuous, restart on failure.
- `research-forecast-{16h,24h,48h}`: hourly staggered eligibility checks; each application retains its native generation horizon.

Use persistent timers, non-overlap locks, UTC timestamps, bounded logs, and runtime data outside Git repositories. Expose no public dashboard ports; review through SSH or loopback tunnelling only.

Every run receipt must contain workload, run ID, Git SHA, configuration hash, UTC start/end, duration, result, output paths/counts, and error summary.

If free disk falls below 20% or 5 GB, whichever is stricter, skip new V3 downloads, record the condition, and never auto-delete research data.

### 5. Validate and expand

- Start V1/V2 with `max-scan=20`; advance through 50, 100, and 200 only after each level completes a scheduled run without overlap, API failures, database errors, or unhealthy disk/memory use. Remain at the last successful level after failure.
- Validate V3 first with BTCUSDT on a known archive date. Confirm correct symbol attribution and that rerunning does not duplicate processing. Then enable BTCUSDT and ETHUSDT for scheduled collection.
- Confirm SQLite integrity and receipt/output count agreement.
- Confirm timer and observer recovery using controlled service restarts. Do not reboot the VM without explicit approval because other VM sessions may be active.
- If the current Windows Meridian process remains stopped, restart only the existing `Meridian_hourly.lnk`. Do not create new Windows tasks or touch `Meridian_hourly_legacy.lnk`.
- Keep existing Windows Meridian and forecast jobs running during comparison. Scanner validation is VM-only.

The shadow comparison is complete only after every selected existing workload has produced at least one comparable successful Windows and VM cycle, including the 48-hour forecast. Compare validity, cutoff timestamps, counts, warnings, and database growth; market-dependent candidate differences are reported, not automatically treated as failures.

### 6. Confirmation-gated cutover

Present the receipts, integrity results, resource evidence, and comparison report. Ask the user:

> Are the cloud collectors and comparison outputs working as intended?

Only after confirmation:

- Disable, but do not delete, the Windows 16h/24h/48h scheduled tasks.
- Disable or move only the current Meridian startup link.
- Stop only processes whose command lines identify the current Meridian launcher.
- Take a final checksummed archive after the Windows writers are quiescent.
- Leave all local data intact.

Rollback is to re-enable the existing Windows tasks/link and disable the cloud units; collected cloud data remains untouched.

## Acceptance Criteria

- All enabled VM collectors run from fresh isolated clones and fresh canonical data stores.
- Meridian records training rows without the null-decimal warning.
- V3 is restart-safe and does not silently replay completed units.
- Run manifests are append-only and match produced files/databases.
- Forecasts retain their 16h/24h/48h behavior.
- No Guard/live-trading paths, secrets, or public ports are introduced.
- Windows collection is disabled only after explicit user confirmation.
- Existing documentation is updated only where operational commands changed; do not create unnecessary Markdown files.
