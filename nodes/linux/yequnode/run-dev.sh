#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
export $(grep -v '^#' .env | xargs)
# Requires: cargo install cargo-watch
exec cargo watch -x "run --release"
