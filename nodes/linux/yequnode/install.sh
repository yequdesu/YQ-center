#!/usr/bin/env bash
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

banner() {
  echo -e "${GREEN}============================================${NC}"
  echo -e "${GREEN}  YeQu Linux Node Installer${NC}"
  echo -e "${GREEN}============================================${NC}"
}

usage() {
  echo "Usage: $0 {user|hybrid|sudo}"
  echo ""
  echo "  user    User-runtime only.  OS permissions constrain capabilities."
  echo "          No sudo access.  ~15 read-only capabilities."
  echo ""
  echo "  hybrid  User + sudo dual runtime.  Read capabilities run as user,"
  echo "          write/artifact capabilities run with sudo.  Full feature set."
  echo ""
  echo "  sudo    Sudo runtime only.  All capabilities run as root."
  echo "          Maximum access, minimal restriction."
  exit 1
}

banner

MODE="${1:-}"
if [ -z "$MODE" ]; then usage; fi
case "$MODE" in user|hybrid|sudo) ;; *) usage ;; esac

ROOT="$(cd "$(dirname "$0")" && pwd)"
SUDOERS_FILE="/etc/sudoers.d/yequnode"

# --- Step 1: Build ---
echo -e "${YELLOW}[1/4] Building release binary ...${NC}"
cd "$ROOT"
cargo build --release 2>&1 | tail -1

# --- Step 2: Install binary ---
echo -e "${YELLOW}[2/4] Installing binary ...${NC}"
sudo cp target/release/yequnode-core /usr/local/bin/yequnode
echo "  -> /usr/local/bin/yequnode"

# --- Step 3: Configure sudo (hybrid / sudo modes) ---
if [ "$MODE" = "hybrid" ] || [ "$MODE" = "sudo" ]; then
  echo -e "${YELLOW}[3/4] Configuring sudo ...${NC}"
  echo "  This grants yequnode full passwordless sudo."
  echo "  If you prefer a restricted sudoers entry, edit $SUDOERS_FILE after install."
  sudo bash -c "echo 'yequnode ALL=(root) NOPASSWD: ALL' > $SUDOERS_FILE"
  sudo chmod 440 "$SUDOERS_FILE"
  echo "  -> $SUDOERS_FILE"
else
  echo -e "${YELLOW}[3/4] Skipping sudo (user mode)${NC}"
fi

# --- Step 4: Config template ---
echo -e "${YELLOW}[4/4] Config template ...${NC}"
CONFIG_DIR="$HOME/.yequnode"
mkdir -p "$CONFIG_DIR"
if [ ! -f "$CONFIG_DIR/config.yaml" ]; then
  cat > "$CONFIG_DIR/config.yaml" <<CONF
node_id: linux-node-01
center_base_url: http://127.0.0.1:9800
yqp_path: /yqp/
log_level: info
db_path: $HOME/.yequnode/jobs.db
node_token: ""
CONF
  echo "  -> $CONFIG_DIR/config.yaml (edit node_token before running)"
else
  echo "  -> $CONFIG_DIR/config.yaml (already exists, skipped)"
fi

echo ""
echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}  Install complete (mode: $MODE)${NC}"
echo -e "${GREEN}============================================${NC}"
echo ""
echo "  Start daemon:  cd $ROOT && ./run.sh"
echo "  Run smoke test: cd $ROOT && ./test-artifact.sh"
echo "  Watch DB:       watch -n 2 $ROOT/../scripts/watch-db.sh"
echo ""
