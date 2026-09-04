#!/usr/bin/env bash
#
# Termux mini-TUI for VM2 (scanner collector / OpenCode host).
#
# Exposes ONLY two top-level actions against VM2:
#   1. Sessions  - list/attach existing tmux sessions, or create a new OpenCode session
#   2. SSH       - open an interactive shell to VM2
#
# This is the Termux-native phone counterpart to the desktop PowerShell TUI.
# No Azure CLI/token is needed on the phone path; VM2 connectivity is plain SSH.
# The connection target is configured below (static public IP by default).

set -uo pipefail

# ---------------------------------------------------------------------------
# Connection configuration (edit to match your VM2)
# ---------------------------------------------------------------------------
VM2_USER="andrei"
VM2_HOST="20.240.249.102"     # VM2 public IP (vm-scanner-collector-01)
SSH_KEY="$HOME/.ssh/vm2_phone" # phone-only keypair, generated on first run
PROJECTS_DIR="/home/andrei/agent/projects"
OPENCODE_BIN="~/.opencode/bin/opencode"

# Termux can auto-launch from any directory; ensure a sane ~/.ssh exists.
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"

# Pick an existing private key: skip .pub/known_hosts/config, prefer ed25519.
find_key() {
    local candidate
    for candidate in "$HOME"/.ssh/id_ed25519 "$HOME"/.ssh/id_rsa "$HOME"/.ssh/*; do
        [ -f "$candidate" ] || continue
        case "$(basename "$candidate")" in
            *.pub|known_hosts|config|authorized_keys) continue ;;
        esac
        SSH_KEY="$candidate"
        return 0
    done
    return 1
}

# Determine the SSH key to use: configured default, then discovered, else generate.
ensure_key() {
    if [ ! -f "$SSH_KEY" ]; then
        if find_key; then
            echo "Using existing phone key: $SSH_KEY"
            echo "(If this is not the key authorized on VM2, edit SSH_KEY at the top of this script.)"
        else
            echo "No phone SSH key found under ~/.ssh/."
            echo "This will create a NEW keypair for the phone and print its public key."
            echo "You must copy that public key into VM2's ~/.ssh/authorized_keys"
            echo "so the phone can log in. It will NOT reuse the desktop secret."
            read -r -p "Generate now? (y/N) " ans
            case "$ans" in
                y|Y|yes|YES)
                    ssh-keygen -t ed25519 -f "$SSH_KEY" -N "" -C "termux-phone" >/dev/null
                    echo ""
                    echo "Public key (append to VM2 ~/.ssh/authorized_keys):"
                    cat "$SSH_KEY.pub"
                    echo ""
                    echo "Verify SSH login to VM2 after adding it, then re-run this menu."
                    read -r -p "Press Enter to continue..."
                    exit 0
                    ;;
                *)
                    echo "No key -> cannot connect. Exiting."
                    exit 1
                    ;;
            esac
        fi
    fi
}

ssh_cmd() {
    # Run a non-interactive command on VM2 using the phone key.
    ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=20 \
        -i "$SSH_KEY" "$VM2_USER@$VM2_HOST" "$@"
}

ssh_tty() {
    # Open an interactive terminal on VM2 running the given command.
    ssh -t -o StrictHostKeyChecking=accept-new -i "$SSH_KEY" "$VM2_USER@$VM2_HOST" "$@"
}

list_sessions() {
    ssh_cmd "tmux list-sessions -F '#S|#W|#{session_windows}|#{session_attached}' 2>/dev/null || true"
}

ping_vm2() {
    # Cheap reachability check; returns 0 if SSH command succeeds.
    ssh_cmd "true" >/dev/null 2>&1
}

# ---------------------------------------------------------------------------
# Sessions menu
# ---------------------------------------------------------------------------
open_sessions_menu() {
    while :; do
        clear
        echo "Sessions"
        echo "========"
        echo ""
        mapfile -t sessions < <(list_sessions)

        if [ ${#sessions[@]} -eq 0 ] || [ -z "${sessions[0]}" ]; then
            echo "No tmux sessions found."
            echo ""
        else
            i=1
            for s in "${sessions[@]}"; do
                IFS='|' read -r name window nwins attached <<<"$s"
                state="detached"
                [ "$attached" = "1" ] && state="attached"
                printf "[%d] %-16s window=%-12s windows=%-4s %s\n" \
                    "$i" "$name" "$window" "$nwins" "$state"
                i=$((i+1))
            done
            echo ""
        fi

        echo "[number] attach session"
        echo "[N]      create new OpenCode session"
        echo "[R]      refresh"
        echo "[B]      back"
        echo ""
        read -r -p "Choose: " choice

        case "${choice^^}" in
            N) new_opencode_session ;;
            R) continue ;;
            B) return ;;
            *)
                if [[ "$choice" =~ ^[0-9]+$ ]] && [ "$choice" -ge 1 ] && \
                   [ "$choice" -le "${#sessions[@]}" ]; then
                    IFS='|' read -r sname _ <<<"${sessions[$((choice-1))]}"
                    ssh_tty "tmux attach -t '$sname'"
                else
                    echo "Unknown session choice."
                    read -r -p "Press Enter to continue..."
                fi
                ;;
        esac
    done
}

new_opencode_session() {
    read -r -p "New session name (default: opencode): " name
    [ -z "$name" ] && name="opencode"
    if ! [[ "$name" =~ ^[A-Za-z0-9_.-]+$ ]]; then
        echo "Use only letters, numbers, dot, underscore, or dash."
        read -r -p "Press Enter to continue..."
        return
    fi
    ssh_cmd "tmux new-session -Ad -s '$name' -c '$PROJECTS_DIR' '$OPENCODE_BIN --auto -c'"
    ssh_tty "tmux attach -t '$name'"
}

# ---------------------------------------------------------------------------
# Top-level menu
# ---------------------------------------------------------------------------
open_main_menu() {
    while :; do
        clear
        echo "Termux VM2 TUI"
        echo "==============="
        echo "Target: $VM2_USER@$VM2_HOST"
        echo ""
        echo "[1] Sessions"
        echo "[2] SSH shell"
        echo "[Q] Quit"
        echo ""
        read -r -p "Choose: " choice

        case "${choice^^}" in
            1)
                if ! ping_vm2; then
                    echo "VM2 is not reachable over SSH. Check that it is running and the host/IP is correct."
                    read -r -p "Press Enter to continue..."
                    continue
                fi
                open_sessions_menu
                ;;
            2)
                ssh_tty ""
                ;;
            Q) exit 0 ;;
            *) echo "Unknown option."; read -r -p "Press Enter to continue..." ;;
        esac
    done
}

ensure_key
open_main_menu
