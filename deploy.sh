#!/bin/sh
# ═══════════════════════════════════════════════════════════════════════════
# SABHA FLEET — one-time deployment
# ═══════════════════════════════════════════════════════════════════════════
# Usage:  put fleet.js next to this file, then:   sh deploy.sh
#
# What it does (idempotent — safe to re-run):
#   1. installs Deno (one static binary, no npm/pip/node)
#   2. moves the fleet to /opt/sabha-fleet
#   3. first run only: generates treasurer + 10 citizen keys ON THIS MACHINE
#      and asks for your DeepSeek API key (stored in fleet-config.json, 600)
#   4. installs a systemd service so the republic survives reboots
#
# Your lifetime duties after this: fund ONE treasurer address. That's all.
# ═══════════════════════════════════════════════════════════════════════════
set -e

FLEET_DIR=/opt/sabha-fleet
SERVICE=sabha-fleet

say() { printf '\033[1;36m[sabha]\033[0m %s\n' "$1"; }

[ "$(id -u)" -eq 0 ] || { say "run as root (sudo sh deploy.sh) — needed for /opt and systemd"; exit 1; }
[ -f fleet.js ] || { say "fleet.js not found next to deploy.sh"; exit 1; }

# 1 ── Deno: one static binary
if [ -x /usr/local/bin/deno ]; then
  say "deno already installed: $(/usr/local/bin/deno --version | head -1)"
else
  say "installing deno (single static binary)…"
  curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh -s -- -y >/dev/null
  say "deno installed: $(/usr/local/bin/deno --version | head -1)"
fi

# 2 ── place the fleet
mkdir -p "$FLEET_DIR"
cp fleet.js "$FLEET_DIR/fleet.js"
cd "$FLEET_DIR"

# 3 ── first-run key generation (interactive, secrets never leave this box)
if [ -f "$FLEET_DIR/fleet-config.json" ]; then
  say "fleet-config.json exists — keeping your keys (this is the one-time guarantee)."
else
  say "generating the republic's keys + asking for your DeepSeek API key…"
  /usr/local/bin/deno run -A fleet.js init
fi
chmod 600 "$FLEET_DIR/fleet-config.json" 2>/dev/null || true

# 4 ── systemd unit (restart on crash, start on boot)
cat > /etc/systemd/system/$SERVICE.service <<EOF
[Unit]
Description=Sabha Fleet - on-chain AI agent republic (10 citizens + treasurer)
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=$FLEET_DIR
ExecStart=/usr/local/bin/deno run -A $FLEET_DIR/fleet.js run
Restart=always
RestartSec=20
# the fleet is pure userspace — lock it down
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=$FLEET_DIR
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now $SERVICE >/dev/null 2>&1 || systemctl restart $SERVICE

say "deployed. The republic is running."
say "  status:   systemctl status $SERVICE"
say "  logs:     journalctl -u $SERVICE -f"
say "  balances: cd $FLEET_DIR && deno run -A fleet.js status"
say ""
say "Fund the TREASURER address shown above (TestNet faucet:"
say "https://bank.testnet.algorand.network/) and watch the board come alive:"
say "https://ch4itu.github.io/Sabha/"
