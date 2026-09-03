# Handoff — Android opencode agent (Termux) · VM2 mini-TUI

> Addressed to: the opencode agent running on the Android phone inside Termux.
> Authored from: the desktop (canonical repo `D:\claude_version_of_scanner`).
> Scope: **VM2 mini-TUI only**. Do not expand into other VM2 work.

## Your job (in one line)
Set up and verify the Termux mini-TUI (`termux/vm2-tui.sh`) on this phone so it
can manage VM2 over SSH with only two actions: **Sessions** and **SSH**.

## Where the code lives
- Repo: `https://github.com/eloqven/claude_version_of_scanner.git`
- If you do not have it yet: `git clone` it (or `git pull` to refresh).
- The script you are responsible for: **`termux/vm2-tui.sh`** (already committed and pushed).
- Please read that file fully before doing anything.

## Target facts (VM2 — do NOT invent or change these)
- Host: `vm-scanner-collector-01`, public IP **`20.240.249.102`**, user **`andrei`**, ARM64 (aarch64).
- opencode on VM2: `~/.opencode/bin/opencode` (NOT on non-interactive PATH — always use the full path).
- Default project dir on VM2: `/home/andrei/agent/projects`.
- The phone uses its **own** SSH key, NEVER the desktop secret. The script
  defaults to `~/.ssh/vm2_phone` on the phone.

## What the script does (already implemented — do not rewrite)
- Top-level menu: `[1] Sessions`, `[2] SSH shell`, `[Q] Quit`.
- `Sessions`: lists tmux sessions, lets you attach by number, or create a new
  OpenCode session via:
  `tmux new-session -Ad -s '<name>' -c /home/andrei/agent/projects '~/.opencode/bin/opencode --auto -c'`
- `SSH shell`: opens an interactive `ssh -t` shell to VM2 using the phone key.
- First run detects a missing phone key and generates a NEW ed25519 keypair,
  printing the public key for you to add to VM2's `~/.ssh/authorized_keys`.

## Your tasks (exact next steps)
1. **Prereqs on the phone:** confirm Termux has `openssh` and `git` installed
   (`pkg install openssh git` if not). Confirm you can reach the repo.
2. **Get the script:** pull/clone the repo so `termux/vm2-tui.sh` exists locally.
3. **SSH key wiring:**
   - Run the script once (or `ssh-keygen -t ed25519 -f ~/.ssh/vm2_phone -N ""`).
   - Read `~/.ssh/vm2_phone.pub`.
   - Add that public key into VM2's `~/.ssh/authorized_keys` for user `andrei`.
     (Use whatever mechanism you have: SSH in with a key you already trust,
     or ask the user to add it from the desktop.)
   - Confirm passwordless SSH to VM2 works:
     `ssh -i ~/.ssh/vm2_phone andrei@20.240.249.102 "true"`
   - Fix the VM2_HOST in the script if the public IP is ever stale/different.
4. **Verify the two actions from the phone:**
   - `Sessions` lists tmux sessions, can attach, and can create a new OpenCode tmux session.
   - `SSH` opens an interactive shell to VM2.
5. **Report** exactly what you changed/confirmed. Follow the repo's
   maintainer/agent workflow in README: report files touched, commands run,
   pass/fail, and remaining unknowns.

## Hard rules
- Do NOT edit the desktop PowerShell TUI (`Azure OpenCode TUI.ps1`) or any
  scanner/dashboard code. This handoff is **TUI-setup on the phone only**.
- Do NOT reuse or expose the desktop SSH private key.
- Do NOT invent VM2 connection values — use the target facts above.
- Do NOT commit changes without the user's explicit request; keep changes local
  on the phone unless told otherwise. Script bug fixes should be noted back to
  the user rather than force-pushed blindly.
- Respect the user constraint: do NOT stack long SSH/Azure calls; build commands
  to make as few round-trips as possible. Where a command may hang, use a timeout.
