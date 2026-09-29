#!/usr/bin/env bash
# Cuttle mesh SSH pairing (Linux/macOS) — per-install identities, explicit authorization.
#
# Cuttle never distributes a shared trusted key: every installation owns its
# keypair (see --generate) and a target machine authorizes exactly the peer
# keys its operator installs. Existing authorized_keys entries are preserved,
# never purged or rotated by this script.
#
#   Per-install identity (safe to re-run, never overwrites):
#     ./install-cuttle-mesh-lan-key.sh --generate
#   Authorize a peer on the TARGET machine:
#     ./install-cuttle-mesh-lan-key.sh --pubkey /path/to/peer.pub --alias peer-name
#
# Installed keys are restricted to your LAN CIDR + loopback via from="...".
set -euo pipefail

LAN_CIDR="192.168.0.0/16"
KEY_FILE="$HOME/.ssh/cuttle_mesh_lan"
PUBKEY=""
ALIAS=""
GENERATE=0

while [ $# -gt 0 ]; do
    case "$1" in
        --generate) GENERATE=1; shift ;;
        --pubkey) PUBKEY="${2:-}"; shift 2 ;;
        --alias) ALIAS="${2:-}"; shift 2 ;;
        --lan-cidr) LAN_CIDR="${2:-}"; shift 2 ;;
        --key-file) KEY_FILE="${2:-}"; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done

key_body() {
    # The key blob (longest whitespace-separated field) for idempotent
    # compare — field positions vary with from="..."/comment decorations.
    echo "$1" | awk '{b="";best=0;for(i=1;i<=NF;i++){if(length($i)>best){best=length($i);b=$i}};print b}'
}

if [ "$GENERATE" -eq 1 ]; then
    if [ -f "$KEY_FILE" ]; then
        echo "Keypair already exists (never overwritten): $KEY_FILE"
    else
        command -v ssh-keygen >/dev/null || { echo "ssh-keygen not found. Install OpenSSH client first." >&2; exit 1; }
        mkdir -p "$(dirname "$KEY_FILE")"
        ssh-keygen -t ed25519 -f "$KEY_FILE" -N '' -C "cuttle-mesh-$(hostname)"
        echo "Generated per-install keypair: $KEY_FILE"
    fi
    if [ -f "$KEY_FILE.pub" ]; then
        echo "Public key (share THIS with pairing targets): $KEY_FILE.pub"
        cat "$KEY_FILE.pub"
    fi
    exit 0
fi

if [ -z "$PUBKEY" ] || [ ! -f "$PUBKEY" ]; then
    echo "Explicit pairing required: pass --pubkey <peer's .pub> [--alias <name>]." >&2
    echo "This script never installs a default/shared key." >&2
    exit 2
fi

PUB="$(tr -d '\r\n' < "$PUBKEY")"
case "$PUB" in
    ssh-*) ;;
    *) echo "Does not look like an OpenSSH public key: $PUBKEY" >&2; exit 2 ;;
esac

PEER="${ALIAS:-$(basename "$PUBKEY" .pub)}"
LINE="from=\"$LAN_CIDR,127.0.0.1,::1\" $PUB cuttle-mesh-peer=\"$PEER\""

AK="$HOME/.ssh/authorized_keys"
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
touch "$AK"
chmod 600 "$AK"

NEW_BODY="$(key_body "$PUB")"
while IFS= read -r existing || [ -n "$existing" ]; do
    [ -z "$existing" ] && continue
    if [ "$(key_body "$existing")" = "$NEW_BODY" ]; then
        echo "Peer key already authorized (idempotent, no change): $PEER"
        exit 0
    fi
done < "$AK"

# Preserve every existing entry — removal is a deliberate manual rotation.
printf '%s\n' "$LINE" >> "$AK"
echo "Authorized LAN-restricted peer key in $AK"
echo "peer=$PEER from=$LAN_CIDR (+ loopback)"
