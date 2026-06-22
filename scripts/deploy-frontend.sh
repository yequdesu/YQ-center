#!/usr/bin/env bash
set -euo pipefail

# ── YeQu Console Deploy Script ──
# Builds the React frontend and syncs to console-dist/ for production serving.
# Usage: ./scripts/deploy-frontend.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
CONSOLE_SRC="$PROJECT_DIR/console-frontend"
CONSOLE_DIST="$PROJECT_DIR/console-dist"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "======================================================"
echo "  YeQu Console -- deploy frontend"
echo "======================================================"

# ── 1. Check source exists ──
if [ ! -d "$CONSOLE_SRC" ]; then
    echo -e "${RED}[ERROR] console-frontend/ not found at $CONSOLE_SRC${NC}"
    exit 1
fi

# ── 2. Install dependencies (only if needed) ──
echo ""
echo -e "${YELLOW}[1/3] Checking dependencies ...${NC}"
cd "$CONSOLE_SRC"
if [ ! -d "node_modules" ]; then
    echo "  Installing npm packages ..."
    npm install
else
    echo "  node_modules exists, skipping install."
fi

# ── 3. Build ──
echo ""
echo -e "${YELLOW}[2/3] Building ...${NC}"
npm run build

# ── 4. Sync to console-dist/ ──
echo ""
echo -e "${YELLOW}[3/3] Build outputs directly to console-dist/ (via vite --outDir)${NC}"
echo ""

echo -e "${GREEN}Done. Frontend deployed to console-dist/.${NC}"
echo "  index.html : $CONSOLE_DIST/index.html"
echo "  assets/    : $(ls "$CONSOLE_DIST/assets" 2>/dev/null | wc -l) files"
echo ""
echo "Restart the Center or reload the browser to see changes."
