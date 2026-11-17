import urllib.request
import urllib.error
import json
import time
import uuid
import os
import subprocess
from datetime import datetime, timezone

GATE_URL = "http://127.0.0.1:8080"
EVIDENCE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "evidence")
os.makedirs(EVIDENCE_DIR, exist_ok=True)

def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

def http_req(method, endpoint, data=None):
    url = f"{GATE_URL}{endpoint}"
    req = urllib.request.Request(url, method=method)
    req.add_header("Content-Type", "application/json")
    body = json.dumps(data).encode("utf-8") if data is not None else None
    
    start_t = time.time()
    try:
        with urllib.request.urlopen(req, data=body, timeout=15) as res:
            res_body = res.read().decode("utf-8")
            elapsed_ms = round((time.time() - start_t) * 1000, 2)
            parsed = json.loads(res_body) if res_body else {}
            return res.status, parsed, elapsed_ms
    except urllib.error.HTTPError as e:
        res_body = e.read().decode("utf-8")
        elapsed_ms = round((time.time() - start_t) * 1000, 2)
        try:
            parsed = json.loads(res_body)
        except Exception:
            parsed = {"raw": res_body}
        return e.code, parsed, elapsed_ms
    except Exception as ex:
        elapsed_ms = round((time.time() - start_t) * 1000, 2)
        return 500, {"error": str(ex)}, elapsed_ms

def test_a10():
    print("\n--- Running Test A10: Crash Recovery / Restart While Action is Held ---")
    log_lines = [
        "=== TEST A10 EVIDENCE: CRASH RECOVERY (STACK RESTART WHILE ACTION HELD) ===",
        f"Timestamp: {now_iso()}"
    ]
    
    conv_id = "conv-customer-a-enquiry"
    # Step 1: Set room to Gated
    http_req("POST", f"/conversations/{conv_id}/mode", {"mode": "gated", "actor_guid": "u-owner-guid"})
    log_lines.append(f"\n[Step 1] Set {conv_id} to GATED mode.")
    
    # Step 2: Dispatch action -> becomes HELD in SQLite WAL
    idempotency_key = f"key-a10-crash-{int(time.time())}"
    disp_payload = {
        "conversation_id": conv_id,
        "idempotency_key": idempotency_key,
        "engine": "n8n",
        "payload": {"enquiry": "Crash recovery verification", "contact": "lead@acme.com", "name": "Alice"},
        "actor_guid": "u-owner-guid",
        "permission_proof": "role:owner"
    }
    status1, body1, ms1 = http_req("POST", "/dispatch", disp_payload)
    action_id = body1.get("action_id")
    log_lines.append(f"\n[Step 2] Action dispatched in Gated mode.")
    log_lines.append(f"  Action ID: {action_id}")
    log_lines.append(f"  State: {body1.get('state')}")
    log_lines.append(f"  Idempotency Key: {idempotency_key}")
    
    # Step 3: Trigger container crash / restart
    log_lines.append(f"\n[Step 3] Executing 'docker compose restart gate' to simulate hard crash/restart...")
    restart_t0 = time.time()
    res = subprocess.run(["docker", "compose", "restart", "gate"], capture_output=True, text=True)
    restart_duration = round(time.time() - restart_t0, 2)
    log_lines.append(f"  Container restarted in {restart_duration}s. Docker output: {res.stdout.strip()}")
    
    # Wait for Gate to resume health
    time.sleep(2)
    for _ in range(15):
        st, b, _ = http_req("GET", "/health")
        if st == 200:
            break
        time.sleep(1)
    log_lines.append(f"  Gate adapter back online and responding healthy.")
    
    # Step 4: Verify Held Action survived in SQLite WAL storage
    status_held, body_held, ms_held = http_req("GET", f"/conversations/{conv_id}/held")
    found_held = any(item.get("action_id") == action_id for item in body_held)
    log_lines.append(f"\n[Step 4] Query Held Queue after restart:")
    log_lines.append(f"  Action {action_id} found intact in queue: {found_held}")
    log_lines.append(f"  Total held items: {len(body_held)}")
    
    # Step 5: Resubmit duplicate dispatch with the same idempotency key
    status_dup, body_dup, ms_dup = http_req("POST", "/dispatch", disp_payload)
    log_lines.append(f"\n[Step 5] Re-dispatched identical payload after crash:")
    log_lines.append(f"  Status: {status_dup} ({ms_dup}ms)")
    log_lines.append(f"  Returned Action ID: {body_dup.get('action_id')}")
    log_lines.append(f"  Returned Status: {body_dup.get('status')}")
    log_lines.append(f"  Returned State: {body_dup.get('state')}")
    
    # Step 6: Release the recovered action
    status_rel, body_rel, ms_rel = http_req("POST", f"/conversations/{conv_id}/decisions", {
        "action_id": action_id,
        "decision": "released",
        "actor_guid": "u-owner-guid"
    })
    log_lines.append(f"\n[Step 6] Release held action after crash recovery:")
    log_lines.append(f"  Release Status: {status_rel} ({ms_rel}ms)")
    log_lines.append(f"  State: {body_rel.get('state')}")
    
    # Clean up: Return to ungated
    http_req("POST", f"/conversations/{conv_id}/mode", {"mode": "ungated", "actor_guid": "u-owner-guid"})
    
    pass_recovery = (
        body1.get("state") == "held" and
        found_held is True and
        body_dup.get("status") == "cached" and
        body_dup.get("action_id") == action_id and
        body_rel.get("state") == "done"
    )
    
    log_lines.append(f"\nVERIFICATION:")
    log_lines.append(f"- Action Persisted Across Crash: {found_held}")
    log_lines.append(f"- Retry After Crash Yielded Cached Result (No Duplicate): {body_dup.get('status') == 'cached'}")
    log_lines.append(f"- Recovered Action Successfully Dispatched: {body_rel.get('state') == 'done'}")
    log_lines.append(f"- Result: {'PASS' if pass_recovery else 'FAIL'}")
    
    out_file = os.path.join(EVIDENCE_DIR, "A10_crash_recovery.txt")
    with open(out_file, "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines) + "\n")
    print(f"Recorded to {out_file}")

if __name__ == "__main__":
    test_a10()
