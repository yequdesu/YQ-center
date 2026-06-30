#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
export $(grep -v '^#' .env | xargs)
exec cargo run --release -- artifact-test
