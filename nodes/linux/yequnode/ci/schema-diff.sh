#!/bin/bash
set -euo pipefail

CENTER_URL="${CENTER_URL:-https://gtw.yequdesu.top}"
SCHEMA_DIR="$(dirname "$0")/../schemas"
mkdir -p "$SCHEMA_DIR"

echo "=== Pulling latest YQP schema from Center ==="

# Pull envelope schema
curl -sf "${CENTER_URL}/admin/schemas/yqp-envelope" \
    -o "$SCHEMA_DIR/yqp-envelope.json" \
    || { echo "WARNING: Could not fetch schema from ${CENTER_URL}"; exit 0; }

echo "=== Schema snapshot saved to $SCHEMA_DIR ==="

# Check if there's a diff from the committed version
if git diff --exit-code "$SCHEMA_DIR/yqp-envelope.json"; then
    echo "Schema unchanged."
else
    echo ""
    echo "=== SCHEMA DRIFT DETECTED ==="
    echo "The YQP envelope schema has changed. Diff:"
    git diff "$SCHEMA_DIR/yqp-envelope.json"
    echo ""
    echo "Action required: Update Rust YqpEnvelope struct in yequnode-core/src/yqp/envelope.rs"
    echo "to match the new schema, then commit the updated schema file."
    exit 1
fi
