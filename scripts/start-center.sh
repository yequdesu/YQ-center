#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

banner() {
  echo -e "${CYAN}======================================================${NC}"
  echo -e "${CYAN}       YeQu Center -- start & observe script          ${NC}"
  echo -e "${CYAN}======================================================${NC}"
}

if [ ! -d ".venv" ]; then
  echo -e "${RED}[ERROR] .venv not found${NC}"
  exit 1
fi

source .venv/bin/activate

PORT="${1:-9800}"
BASE_URL="http://127.0.0.1:${PORT}"

case "${2:-start}" in
  start)
    banner

    # Ensure PostgreSQL is running
    if ! docker compose ps postgres 2>/dev/null | grep -q "healthy"; then
      echo "Starting PostgreSQL..."
      docker compose up -d postgres
      sleep 3
    fi

    echo ""
    echo -e "${YELLOW}[1/4] alembic migrate ...${NC}"
    alembic upgrade head 2>&1 | tail -1

    echo ""
    echo -e "${YELLOW}[2/4] Provision winClient node ...${NC}"
    python src/yequ/main.py &
    PID=$!
    sleep 3

    TOKEN="winc-token-4a7f3c9e1b2d8f6c"
    curl -s -X POST "${BASE_URL}/admin/nodes" \
      -H "Content-Type: application/json" \
      -d '{"node_id":"winClient","node_name":"Windows Client","token":"'"${TOKEN}"'","role":"compute","locality":"lan"}' \
      | python3 -m json.tool

    echo ""
    echo -e "${GREEN}------------------------------------------------------${NC}"
    echo -e "${GREEN}  Center running: ${BASE_URL}${NC}"
    echo -e "${GREEN}  PID: ${PID}${NC}"
    echo -e "${GREEN}  health: curl ${BASE_URL}/healthz${NC}"
    echo -e "${GREEN}------------------------------------------------------${NC}"
    echo ""
    echo -e "${CYAN}Press Ctrl+C to stop${NC}"
    echo ""
    wait $PID
    ;;

  status)
    banner
    echo ""
    echo -e "${YELLOW}-- health check --${NC}"
    curl -s "${BASE_URL}/healthz" | python3 -m json.tool 2>/dev/null || echo -e "${RED}Center not running${NC}"
    echo ""
    echo -e "${YELLOW}-- provisioned nodes --${NC}"
    curl -s "${BASE_URL}/admin/nodes" 2>/dev/null | python3 -m json.tool 2>/dev/null || echo -e "${RED}Cannot get nodes${NC}"
    ;;

  test-hello)
    banner
    TOKEN="winc-token-4a7f3c9e1b2d8f6c"
    echo -e "${YELLOW}-- node.hello --${NC}"
    curl -s -X POST "${BASE_URL}/yqp/" \
      -H "Content-Type: application/json" \
      -H "Authorization: Bearer ${TOKEN}" \
      -d '{"yqp_version":"0.1","message_id":"msg_hello_'$(date +%s)'","message_type":"node.hello","trace_id":"tr_hello_'$(date +%s)'","node_id":"winClient","timestamp":"'$(date -u +%Y-%m-%dT%H:%M:%SZ)'","payload":{"daemon_version":"0.1.0","node_name":"Windows Client","role":["compute"],"locality":"lan","platform":{"os":"windows","arch":"amd64"}}}' \
      | python3 -m json.tool
    ;;

  test-full)
    banner
    TOKEN="winc-token-4a7f3c9e1b2d8f6c"
    NODE="winClient"
    AUTH="Authorization: Bearer ${TOKEN}"
    MSG_PREFIX="msg_$(date +%s)"
    TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

    echo -e "${YELLOW}[1] node.hello${NC}"
    curl -s -X POST "${BASE_URL}/yqp/" -H "Content-Type: application/json" -H "${AUTH}" \
      -d '{"yqp_version":"0.1","message_id":"'"${MSG_PREFIX}"'_hello","message_type":"node.hello","trace_id":"tr_1","node_id":"'"${NODE}"'","timestamp":"'"${TS}"'","payload":{"daemon_version":"0.1.0","platform":{"os":"windows","arch":"amd64"}}}' \
      | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  -> {d[\"message_type\"]}')"

    echo -e "${YELLOW}[2] node.register_capabilities${NC}"
    curl -s -X POST "${BASE_URL}/yqp/" -H "Content-Type: application/json" -H "${AUTH}" \
      -d '{"yqp_version":"0.1","message_id":"'"${MSG_PREFIX}"'_reg","message_type":"node.register_capabilities","trace_id":"tr_2","node_id":"'"${NODE}"'","timestamp":"'"${TS}"'","payload":{"plugins":[{"plugin_id":"system.metrics","plugin_version":"0.1.0","functions":[{"name":"system.metrics.snapshot","input_schema":{"type":"object","properties":{}},"output_schema":{"type":"object","properties":{"cpu":{"type":"number"},"memory":{"type":"number"}}},"risk":"safe","effect":"read","timeout_sec":5,"idempotency":"idempotent"}],"signals":[{"name":"system.cpu.usage","scope":"node","ttl_sec":15,"value_schema":{"type":"number","minimum":0,"maximum":100}}]}]}}' \
      | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  -> registered={d[\"payload\"][\"registered_count\"]}')"

    echo -e "${YELLOW}[3] node.heartbeat${NC}"
    curl -s -X POST "${BASE_URL}/yqp/" -H "Content-Type: application/json" -H "${AUTH}" \
      -d '{"yqp_version":"0.1","message_id":"'"${MSG_PREFIX}"'_hb","message_type":"node.heartbeat","trace_id":"tr_3","node_id":"'"${NODE}"'","timestamp":"'"${TS}"'","payload":{"daemon_uptime_sec":60,"running_jobs":0,"plugin_count":1,"status":"online"}}' \
      | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  -> {d[\"message_type\"]} OK')"

    echo -e "${YELLOW}[4] signal.report${NC}"
    curl -s -X POST "${BASE_URL}/yqp/" -H "Content-Type: application/json" -H "${AUTH}" \
      -d '{"yqp_version":"0.1","message_id":"'"${MSG_PREFIX}"'_sig","message_type":"signal.report","trace_id":"tr_4","node_id":"'"${NODE}"'","timestamp":"'"${TS}"'","payload":{"signals":[{"name":"system.cpu.usage","scope":"node","value":42.5,"collected_at":"'"${TS}"'","ttl_sec":15}]}}' \
      | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  -> accepted={d[\"payload\"][\"accepted\"]} rejected={d[\"payload\"][\"rejected\"]}')"

    echo -e "${YELLOW}[5] job.poll (empty)${NC}"
    curl -s -X POST "${BASE_URL}/yqp/" -H "Content-Type: application/json" -H "${AUTH}" \
      -d '{"yqp_version":"0.1","message_id":"'"${MSG_PREFIX}"'_poll","message_type":"job.poll","trace_id":"tr_5","node_id":"'"${NODE}"'","timestamp":"'"${TS}"'","payload":{"capacity":2,"running_jobs":[]}}' \
      | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  -> {d[\"message_type\"]} jobs={len(d[\"payload\"][\"jobs\"])}')"

    echo ""
    echo -e "${GREEN}-- Node protocol test complete --${NC}"
    ;;

  test-stage3)
    banner
    TOKEN="winc-token-4a7f3c9e1b2d8f6c"
    NODE="winClient"
    AUTH="Authorization: Bearer ${TOKEN}"
    TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

    echo -e "${YELLOW}[Stage 3] Invocation -> Job -> Poll -> Accept -> Finish${NC}"

    echo -e "${YELLOW}[1] POST /admin/invocations${NC}"
    INV=$(curl -s -X POST "${BASE_URL}/admin/invocations" \
      -H "Content-Type: application/json" \
      -d '{"function_name":"system.metrics.snapshot","target_node_id":"'"${NODE}"'","input_payload":{},"timeout_sec":30}')
    echo "  -> $INV"
    JOB_ID=$(echo "$INV" | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])")
    echo "  job_id=$JOB_ID"

    echo -e "${YELLOW}[2] job.poll${NC}"
    curl -s -X POST "${BASE_URL}/yqp/" -H "Content-Type: application/json" -H "${AUTH}" \
      -d '{"yqp_version":"0.1","message_id":"msg_s3_poll","message_type":"job.poll","trace_id":"tr_s3","node_id":"'"${NODE}"'","timestamp":"'"${TS}"'","payload":{"capacity":2,"running_jobs":[]}}' \
      | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  -> {d[\"message_type\"]} jobs={len(d[\"payload\"][\"jobs\"])}')"

    echo -e "${YELLOW}[3] job.accepted${NC}"
    curl -s -X POST "${BASE_URL}/yqp/" -H "Content-Type: application/json" -H "${AUTH}" \
      -d '{"yqp_version":"0.1","message_id":"msg_s3_acc","message_type":"job.accepted","trace_id":"tr_s3","node_id":"'"${NODE}"'","timestamp":"'"${TS}"'","payload":{"job_id":"'"${JOB_ID}"'"}}' \
      | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  -> {d[\"payload\"][\"status\"]}')"

    echo -e "${YELLOW}[4] job.finished${NC}"
    curl -s -X POST "${BASE_URL}/yqp/" -H "Content-Type: application/json" -H "${AUTH}" \
      -d '{"yqp_version":"0.1","message_id":"msg_s3_fin","message_type":"job.finished","trace_id":"tr_s3","node_id":"'"${NODE}"'","timestamp":"'"${TS}"'","payload":{"job_id":"'"${JOB_ID}"'","status":"succeeded","output":{"cpu":42.5,"memory":60.2}}}' \
      | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  -> {d[\"payload\"][\"status\"]}')"

    echo ""
    echo -e "${GREEN}-- Stage 3 test complete --${NC}"
    ;;

  test-stage4)
    banner
    echo -e "${YELLOW}[Stage 4] Agent Session -> Invoke${NC}"

    echo -e "${YELLOW}[1] POST /agent/sessions${NC}"
    SESS=$(curl -s -X POST "${BASE_URL}/agent/sessions" \
      -H "Content-Type: application/json" \
      -d '{"actor_id":"test-agent","execution_mode":"auto"}')
    echo "  -> $SESS"
    SID=$(echo "$SESS" | python3 -c "import sys,json; print(json.load(sys.stdin)['session_id'])")

    echo -e "${YELLOW}[2] POST /agent/invoke${NC}"
    curl -s -X POST "${BASE_URL}/agent/invoke" \
      -H "Content-Type: application/json" \
      -d '{"session_id":"'"${SID}"'","prompt":"get system metrics","execution_mode":"auto"}' \
      | python3 -m json.tool

    echo ""
    echo -e "${GREEN}-- Stage 4 test complete --${NC}"
    ;;

  test-all)
    $0 "$PORT" test-full
    $0 "$PORT" test-stage3
    $0 "$PORT" test-stage4
    ;;

  *)
    echo "Usage: $0 [port] {start|status|test-hello|test-full|test-stage3|test-stage4|test-all}"
    echo ""
    echo "  start        Start Center + provision winClient"
    echo "  status       Health check + provisioned nodes"
    echo "  test-hello   node.hello"
    echo "  test-full    Node protocol full flow (Stage 2)"
    echo "  test-stage3  Job Runtime: Invocation->Job->Poll->Finish (Stage 3)"
    echo "  test-stage4  Agent: Session + Invoke (Stage 4)"
    echo "  test-all     Run test-full + test-stage3 + test-stage4"
    ;;
esac
