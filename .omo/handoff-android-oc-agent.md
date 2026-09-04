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

## Target facts (VM2 — the IP below is a CANDIDATE, not gospel)
- Host: `vm-scanner-collector-01`, user **`andrei`**, ARM64 (aarch64).
- opencode on VM2: `~/.opencode/bin/opencode` (NOT on non-interactive PATH — always use the full path).
- Default project dir on VM2: `/home/andrei/agent/projects`.
- The phone uses its **own** SSH key, NEVER the desktop secret.

## SSH keys already authorized on the VMs (do NOT add more)
The desktop agent already installed TWO phone public keys into andrei's
`/home/andrei/.ssh/authorized_keys` on **both VM1 and VM2** (appended, existing
desktop RSA key preserved). Their comments:
- Key A: `phone-andrei` (`ssh-ed25519 ... phone-andrei`)
- Key B: `termux-vm2-phone` (`ssh-ed25519 ... termux-vm2-phone`)

Your job for the phone side is NOT to install keys again. Instead:
1. Run `ls -la ~/.ssh/` on this phone to see what private key files exist.
2. The private key that pairs with a given public key has the SAME key material —
   you can match it by comparing. If a matching private key exists, use it.
3. Do NOT assume a key name like `vm2_phone`. Find what is actually there.
4. If NO private key on this phone matches `phone-andrei` / `termux-vm2-phone`,
   then those public keys did NOT come from this phone. In that case, generate a
   NEW phone keypair (`ssh-keygen -t ed25519 -f ~/.ssh/<new-name> -N ""`), print
   its `.pub`, and ask the user to have the desktop agent add that new pubkey to
   the VMs' authorized_keys. Do NOT wrap up until the phone can log in.

## Useful resource on VM2 (mentioned for later, not needed for TUI setup)
- There is a PAT (personal access token) file at **`~/agent/projects/bed`** on
  VM2. It is NOT needed to set up or verify the mini-TUI, but it may be useful
  further down the line. Do not go out of your way to use it now; just know it
  exists.

## Public IP handling — READ THIS FIRST (do not skip)
- The script and this handoff both store a public IP: **`20.240.249.102`**.
  This value is a **snapshot that may be stale**. Azure public IPs are not
  guaranteed static — the address can change if/when the VM is deallocated and
  started again (compute is stopped to save money, which risks an IP change).
- The phone has NO Azure CLI (locked decision), so you cannot query Azure for
  the live IP yourself.
- **Therefore, at the very start of your session, ASK THE USER to confirm the
  current public IP of VM2** (the user can read it from the desktop, e.g.
  `az vm show -g rg-scanner-collector-swe -n vm-scanner-collector-01 --show-details`,
  or from the Azure portal). Do NOT silently trust the stored value.
- Once the user confirms the current IP:
  - If it matches `20.240.249.102`, keep it.
  - If it differs, update `VM2_HOST` in `termux/vm2-tui.sh` and use the confirmed
    value everywhere below.
- Treat any IP you were handed as unverified until the user confirms it. Never
  hardcode a value you have not confirmed for THIS session.

## What the script does (already implemented — do not rewrite)
- Top-level menu: `[1] Sessions`, `[2] SSH shell`, `[Q] Quit`.
- `Sessions`: lists tmux sessions, lets you attach by number, or create a new
  OpenCode session via:
  `tmux new-session -Ad -s '<name>' -c /home/andrei/agent/projects '~/.opencode/bin/opencode --auto -c'`
- `SSH shell`: opens an interactive `ssh -t` shell to VM2 using the phone key.
- NOTE: the script's default key path (`~/.ssh/vm2_phone`) is a placeholder and
  may NOT match the phone's real key. If the phone's key is named differently,
  update `SSH_KEY` near the top of the script to the real path.

## Your tasks (exact next steps)
1. **Prereqs on the phone:** confirm Termux has `openssh` and `git` installed
   (`pkg install openssh git` if not). Confirm you can reach the repo.
2. **Get the script:** pull/clone the repo so `termux/vm2-tui.sh` exists locally.
3. **SSH key wiring (phone side only — pubkeys already on the VMs):**
   - Run `ls -la ~/.ssh/` and identify the phone's existing private key that
     pairs with `phone-andrei` OR `termux-vm2-phone` (see "SSH keys already
     authorized" above). Use the real filename — do NOT assume `vm2_phone`.
   - Confirm passwordless SSH to VM2 works, using the **user-confirmed** IP and
     the REAL key name:
     `ssh -i ~/.ssh/<REAL_KEY_NAME> andrei@<CONFIRMED_VM2_IP> "true"`
   - If no matching private key exists on the phone, generate a fresh one and
     have the user's desktop agent add its pubkey to both VMs (per the section
     above). Do not finish until the phone can actually log in.
   - Update the key path used by `termux/vm2-tui.sh` to the REAL key name if the
     script's default does not match.
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
