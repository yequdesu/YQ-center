#!/usr/bin/env python3
"""E2E verification: Center + winClient full chain.

Usage:
  python scripts/e2e_win_node.py [--base-url http://192.168.1.25:9800] [--timeout 60]

Verifies:
  1. GET /healthz
  2. winClient node exists and is online
  3. Creates Invocation (system.metrics.snapshot on winClient)
  4. Waits for Job to succeed
  5. Prints invocation_id, job_id, result, timeline events
  6. Creates Agent Session + Invokes Agent
  7. Verifies Agent success
"""

import argparse
import sys
import time
from datetime import UTC, datetime

import httpx


def e2e(base_url: str, timeout_sec: int) -> bool:
    """Run the full E2E verification. Returns True on success."""
    node_id = "winClient"
    ok = 0
    fail = 0

    def step(name: str):
        print(f"\n{'='*60}")
        print(f"  {name}")
        print(f"{'='*60}")

    def check(cond: bool, msg: str):
        nonlocal ok, fail
        if cond:
            ok += 1
            print(f"  [PASS] {msg}")
        else:
            fail += 1
            print(f"  [FAIL] {msg}")

    job_id = None
    inv_id = None
    sid = None

    try:
        # ── 1. Health check ──
        step("1. Health Check")
        r = httpx.get(f"{base_url}/healthz", timeout=10)
        check(r.status_code == 200, f"GET /healthz → {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            check(data["status"] == "ok", f"status={data['status']} db={data['database']}")

        # ── 2. Node status ──
        step("2. Node Status")
        r = httpx.get(f"{base_url}/admin/nodes/{node_id}", timeout=10)
        if r.status_code == 200:
            node = r.json()
            check(node["node_id"] == node_id, f"node_id={node['node_id']}")
            check(node["status"] in ("online", "provisioned"),
                  f"status={node['status']} (last_seen={node.get('last_seen_at', 'never')})")
        elif r.status_code == 404:
            check(False, f"Node {node_id!r} not found — did you run start-center.sh?")
            print("  [INFO] Try: scripts/start-center.sh")
        else:
            check(False, f"GET /admin/nodes/{node_id} → {r.status_code} {r.text[:200]}")

        # ── 3. Create Invocation ──
        step("3. Create Invocation (system.metrics.snapshot)")
        r = httpx.post(f"{base_url}/admin/invocations", json={
            "function_name": "system.metrics.snapshot",
            "target_node_id": node_id,
            "input_payload": {},
            "timeout_sec": 30,
        }, timeout=10)
        check(r.status_code == 201, f"POST /admin/invocations → {r.status_code}")
        inv_data = {}
        if r.status_code == 201:
            inv_data = r.json()
            job_id = inv_data["job_id"]
            inv_id = inv_data["invocation_id"]
            check(bool(inv_id), f"invocation_id={inv_id}")
            check(bool(job_id), f"job_id={job_id}")
            check(inv_data["job_status"] == "queued", f"job_status={inv_data['job_status']}")

        # ── 4. Wait for job to finish ──
        step("4. Wait for Job Completion")
        if job_id:
            deadline = time.time() + timeout_sec
            final_status = None
            while time.time() < deadline:
                r = httpx.get(f"{base_url}/admin/jobs/{job_id}", timeout=5)
                if r.status_code == 200:
                    final_status = r.json()["status"]
                    print(f"  job.status = {final_status}")
                    if final_status in ("succeeded", "failed", "cancelled", "timeout"):
                        break
                time.sleep(2)
            check(final_status == "succeeded", f"final job status = {final_status}")

            # Print job detail
            r = httpx.get(f"{base_url}/admin/jobs/{job_id}", timeout=5)
            if r.status_code == 200:
                job = r.json()
                if job.get("output"):
                    print(f"  output: {job['output']}")

        # ── 5. Invocation detail ──
        step("5. Invocation Detail")
        if inv_id:
            r = httpx.get(f"{base_url}/admin/invocations/{inv_id}", timeout=10)
            check(r.status_code == 200, f"GET /admin/invocations/{inv_id} → {r.status_code}")
            if r.status_code == 200:
                inv = r.json()
                check(inv["status"] in ("succeeded", "running"),
                      f"invocation_status={inv['status']}")
                check(len(inv.get("jobs", [])) >= 1,
                      f"associated jobs: {len(inv.get('jobs', []))}")
                print(f"  function={inv['function_name']} status={inv['status']}")

        # ── 6. Timeline ──
        step("6. Timeline Events")
        if job_id:
            r = httpx.get(f"{base_url}/admin/timeline?job_id={job_id}", timeout=10)
            check(r.status_code == 200, f"GET /admin/timeline?job_id={job_id} → {r.status_code}")
            if r.status_code == 200:
                events = r.json()
                check(len(events) >= 1, f"timeline events for this job: {len(events)}")
                for e in events:
                    print(f"  [{e['global_seq']}] {e['event_type']}")

        # ── 7. Agent Session + Invoke ──
        step("7. Agent Session + Invoke")
        r = httpx.post(f"{base_url}/agent/sessions", json={
            "actor_id": "e2e-test",
            "execution_mode": "auto",
        }, timeout=10)
        check(r.status_code == 201, f"POST /agent/sessions → {r.status_code}")
        if r.status_code == 201:
            sid = r.json()["session_id"]
            print(f"  session_id={sid}")

        if sid:
            r = httpx.post(f"{base_url}/agent/invoke", json={
                "session_id": sid,
                "provider_name": "fake",
                "prompt": "get system metrics",
                "execution_mode": "auto",
            }, timeout=10)
            check(r.status_code == 200, f"POST /agent/invoke → {r.status_code}")
            if r.status_code == 200:
                data = r.json()
                check(data["success"] is True, f"agent success={data['success']}")
                if data.get("function_calls"):
                    for fc in data["function_calls"]:
                        print(f"  function_call: {fc['name']}")

    except httpx.ConnectError as e:
        print(f"\n[FATAL] Cannot connect to {base_url}: {e}")
        return False
    except Exception as e:
        print(f"\n[FATAL] Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return False

    # Final summary
    total = ok + fail
    print(f"\n{'='*60}")
    print(f"  Results: {ok}/{total} passed")
    print(f"{'='*60}")
    return fail == 0


def main():
    parser = argparse.ArgumentParser(description="YeQu Center E2E Verification")
    parser.add_argument("--base-url", default="http://127.0.0.1:9800",
                        help="Center base URL (default: http://127.0.0.1:9800)")
    parser.add_argument("--timeout", type=int, default=60,
                        help="Max wait for job completion (seconds, default: 60)")
    args = parser.parse_args()

    print(f"Center: {args.base_url}")
    print(f"Time:   {datetime.now(UTC).isoformat()}")

    success = e2e(args.base_url, args.timeout)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
