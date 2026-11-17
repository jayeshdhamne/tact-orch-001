import os
import uuid
import json
import asyncio
import logging
from datetime import datetime, timezone
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Header, Depends, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import httpx

from database import init_db, get_db
from nats_client import init_nats, publish_event, close_nats

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gate")

# Environment configuration
N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL", "")
N8N_WEBHOOK_SECRET = os.getenv("N8N_WEBHOOK_SECRET", "")
CALNODE_URL = os.getenv("CALNODE_URL", "http://calnode:3000")
CALNODE_API_KEY = os.getenv("CALNODE_API_KEY", "")
WINDMILL_URL = os.getenv("WINDMILL_URL", "http://windmill-server:8000")
WINDMILL_TOKEN = os.getenv("WINDMILL_TOKEN", "")
WINDMILL_WORKSPACE = os.getenv("WINDMILL_WORKSPACE", "safi")

# Per-conversation lock dictionary
conversation_locks: dict[str, asyncio.Lock] = {}

def get_conversation_lock(conversation_id: str) -> asyncio.Lock:
    if conversation_id not in conversation_locks:
        conversation_locks[conversation_id] = asyncio.Lock()
    return conversation_locks[conversation_id]

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize DB & NATS
    await init_db()
    await init_nats()
    
    # Startup Recovery Check (SOW Test A10)
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM actions WHERE state = 'dispatching'")
        uncertain_actions = await cursor.fetchall()
        for action in uncertain_actions:
            logger.warning("Recovery: Action %s in state 'dispatching' marked as 'uncertain'", action["id"])
            await db.execute(
                "UPDATE actions SET state = 'uncertain', outcome = ? WHERE id = ?",
                (json.dumps({"error": "server_restarted_during_dispatch"}), action["id"])
            )
        await db.commit()
    finally:
        await db.close()

    yield

    # Shutdown
    await close_nats()

app = FastAPI(title="SAFI Local Gate Adapter", lifespan=lifespan)

# --- Pydantic Schemas ---
class DispatchRequest(BaseModel):
    conversation_id: str
    idempotency_key: str
    engine: str  # 'n8n', 'calnode', 'windmill'
    payload: dict
    actor_guid: str
    permission_proof: str
    meeting_port_context: dict = {}

class ModeChangeRequest(BaseModel):
    mode: str  # 'ungated' or 'gated'
    actor_guid: str

class DecisionRequest(BaseModel):
    action_id: str
    decision: str  # 'released' or 'cancelled'
    actor_guid: str

# --- Helper: Permission Verification (Test A09) ---
async def verify_permission(actor_guid: str, required_role: str = "owner"):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT role, is_revoked FROM permissions WHERE user_id = ?", (actor_guid,))
        user = await cursor.fetchone()
        if not user or user["is_revoked"] == 1:
            raise HTTPException(status_code=403, detail="Forbidden: Unauthorised or permission revoked")
        if user["role"] != required_role:
            raise HTTPException(status_code=403, detail="Forbidden: Insufficient privileges")
    finally:
        await db.close()

# --- External Engine Dispatch Handlers ---
async def dispatch_to_engine(engine: str, payload: dict, idempotency_key: str) -> dict:
    async with httpx.AsyncClient(timeout=15.0) as client:
        if engine == "n8n":
            if not N8N_WEBHOOK_URL:
                return {"status": "mocked", "engine": "n8n", "detail": "N8N_WEBHOOK_URL not set"}
            headers = {
                "Content-Type": "application/json",
                "X-Gate-Secret": N8N_WEBHOOK_SECRET,
                "X-Idempotency-Key": idempotency_key
            }
            res = await client.post(N8N_WEBHOOK_URL, json=payload, headers=headers)
            res.raise_for_status()
            return res.json() if res.content else {"status": "success", "code": res.status_code}

        elif engine == "calnode":
            action_type = payload.get("action", "slots")
            headers = {
                "Authorization": f"Bearer {CALNODE_API_KEY}",
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key
            }
            if action_type == "slots":
                event_slug = payload.get("slug", "intro-call")
                res = await client.get(f"{CALNODE_URL}/v1/event-types/{event_slug}/slots", headers=headers)
                return res.json()
            elif action_type == "book":
                res = await client.post(f"{CALNODE_URL}/v1/bookings", json=payload.get("booking_data", {}), headers=headers)
                return res.json()
            else:
                raise ValueError(f"Unknown calnode action: {action_type}")

        elif engine == "windmill":
            script_path = payload.get("script_path", "u/admin/assign_task")
            headers = {
                "Authorization": f"Bearer {WINDMILL_TOKEN}",
                "Content-Type": "application/json"
            }
            run_url = f"{WINDMILL_URL}/api/w/{WINDMILL_WORKSPACE}/jobs/run/p/{script_path}"
            res = await client.post(run_url, json=payload.get("args", {}), headers=headers)
            res.raise_for_status()
            
            # Windmill returns either job_id as string or json
            try:
                data = res.json()
                if isinstance(data, str):
                    return {"job_id": data, "status": "ASSIGNED", "engine": "windmill"}
                return data
            except Exception:
                return {"job_id": res.text.strip('"'), "status": "ASSIGNED", "engine": "windmill"}

        else:
            raise ValueError(f"Unsupported engine: {engine}")

# --- API Endpoints ---

from livekit import api as livekit_api

LIVEKIT_URL = os.getenv("LIVEKIT_URL", "")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "")

@app.get("/token")
async def get_livekit_token(room: str, identity: str = "u-owner-guid"):
    token = (
        livekit_api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(identity)
        .with_name(identity)
        .with_grants(livekit_api.VideoGrants(room_join=True, room=room))
        .to_jwt()
    )
    return {"token": token, "url": LIVEKIT_URL, "room": room, "identity": identity}

@app.get("/health")
async def health():
    return {"status": "healthy", "service": "safi-gate-adapter"}

# Serve SafiChat UI
@app.get("/")
async def serve_ui():
    static_file = os.path.join(os.path.dirname(__file__), "static", "index.html")
    if os.path.exists(static_file):
        return FileResponse(static_file)
    return {"message": "SafiChat Gate running. Static UI not found."}

@app.post("/dispatch")
async def dispatch(req: DispatchRequest):
    # 1. Re-check permissions immediately (SOW §6)
    await verify_permission(req.actor_guid, "owner")

    lock = get_conversation_lock(req.conversation_id)
    async with lock:
        db = await get_db()
        try:
            # 2. Check Idempotency key
            cursor = await db.execute("SELECT * FROM actions WHERE idempotency_key = ?", (req.idempotency_key,))
            existing = await cursor.fetchone()
            if existing:
                return {
                    "action_id": existing["id"],
                    "status": "cached",
                    "state": existing["state"],
                    "outcome": json.loads(existing["outcome"]) if existing["outcome"] else None
                }

            # 3. Read current conversation mode
            cursor = await db.execute("SELECT mode, subject_line FROM conversation_mode WHERE conversation_id = ?", (req.conversation_id,))
            conv = await cursor.fetchone()
            if not conv:
                raise HTTPException(status_code=404, detail="ConversationID not found")

            action_id = str(uuid.uuid4())
            now_iso = datetime.now(timezone.utc).isoformat()
            current_mode = conv["mode"]

            # 4. Gated Mode -> Hold action
            if current_mode == "gated":
                await db.execute(
                    """INSERT INTO actions (id, conversation_id, idempotency_key, state, engine, payload, actor_guid, permission_proof, created_at)
                       VALUES (?, ?, ?, 'held', ?, ?, ?, ?, ?)""",
                    (action_id, req.conversation_id, req.idempotency_key, req.engine, json.dumps(req.payload), req.actor_guid, req.permission_proof, now_iso)
                )
                await db.commit()

                # Publish JetStream event
                await publish_event("action.held", {
                    "operation_id": action_id,
                    "idempotency_key": req.idempotency_key,
                    "conversation_id": req.conversation_id,
                    "subject_line": conv["subject_line"],
                    "actor_guid": req.actor_guid,
                    "permission_provenance": req.permission_proof,
                    "meeting_port_context": req.meeting_port_context,
                    "engine": req.engine,
                    "timestamp": now_iso
                })

                return {"action_id": action_id, "state": "held", "message": "Action held pending User approval"}

            # 5. Ungated Mode -> Execute immediately
            await db.execute(
                """INSERT INTO actions (id, conversation_id, idempotency_key, state, engine, payload, actor_guid, permission_proof, created_at)
                   VALUES (?, ?, ?, 'dispatching', ?, ?, ?, ?, ?)""",
                (action_id, req.conversation_id, req.idempotency_key, req.engine, json.dumps(req.payload), req.actor_guid, req.permission_proof, now_iso)
            )
            await db.commit()

            try:
                outcome = await dispatch_to_engine(req.engine, req.payload, req.idempotency_key)
                outcome_json = json.dumps(outcome)
                await db.execute(
                    "UPDATE actions SET state = 'done', outcome = ? WHERE id = ?",
                    (outcome_json, action_id)
                )
                await db.commit()

                await publish_event("action.dispatched", {
                    "operation_id": action_id,
                    "idempotency_key": req.idempotency_key,
                    "conversation_id": req.conversation_id,
                    "subject_line": conv["subject_line"],
                    "actor_guid": req.actor_guid,
                    "permission_provenance": req.permission_proof,
                    "meeting_port_context": req.meeting_port_context,
                    "engine": req.engine,
                    "outcome": outcome,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })

                return {"action_id": action_id, "state": "done", "outcome": outcome}
            except Exception as e:
                err_outcome = {"error": str(e)}
                await db.execute(
                    "UPDATE actions SET state = 'uncertain', outcome = ? WHERE id = ?",
                    (json.dumps(err_outcome), action_id)
                )
                await db.commit()
                raise HTTPException(status_code=502, detail=f"Engine dispatch error: {e}")
        finally:
            await db.close()

@app.post("/conversations/{conversation_id}/mode")
async def change_mode(conversation_id: str, req: ModeChangeRequest):
    # Barge In / Return to Ungated mode change
    await verify_permission(req.actor_guid, "owner")

    if req.mode not in ("ungated", "gated"):
        raise HTTPException(status_code=400, detail="Invalid mode. Must be 'ungated' or 'gated'")

    lock = get_conversation_lock(conversation_id)
    async with lock:
        db = await get_db()
        try:
            # Check for in-flight dispatching actions (Test A08 race handling)
            cursor = await db.execute(
                "SELECT COUNT(*) as inflight FROM actions WHERE conversation_id = ? AND state = 'dispatching'",
                (conversation_id,)
            )
            row = await cursor.fetchone()
            inflight_count = row["inflight"] if row else 0

            now_iso = datetime.now(timezone.utc).isoformat()
            await db.execute(
                "UPDATE conversation_mode SET mode = ?, updated_at = ?, updated_by = ? WHERE conversation_id = ?",
                (req.mode, now_iso, req.actor_guid, conversation_id)
            )
            await db.commit()

            cursor = await db.execute("SELECT subject_line FROM conversation_mode WHERE conversation_id = ?", (conversation_id,))
            conv = await cursor.fetchone()

            await publish_event("mode.changed", {
                "conversation_id": conversation_id,
                "subject_line": conv["subject_line"] if conv else "",
                "new_mode": req.mode,
                "actor_guid": req.actor_guid,
                "accounted_inflight": inflight_count,
                "timestamp": now_iso
            })

            return {
                "conversation_id": conversation_id,
                "mode": req.mode,
                "in_flight_accounted": inflight_count,
                "confirmed_at": now_iso
            }
        finally:
            await db.close()

@app.get("/conversations/{conversation_id}/held")
async def get_held_actions(conversation_id: str):
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, idempotency_key, engine, payload, created_at FROM actions WHERE conversation_id = ? AND state = 'held'",
            (conversation_id,)
        )
        rows = await cursor.fetchall()
        return [
            {
                "action_id": r["id"],
                "idempotency_key": r["idempotency_key"],
                "engine": r["engine"],
                "payload": json.loads(r["payload"]),
                "created_at": r["created_at"]
            }
            for r in rows
        ]
    finally:
        await db.close()

@app.post("/conversations/{conversation_id}/decisions")
async def decide_action(conversation_id: str, req: DecisionRequest):
    # Approve / Release or Cancel a held action (Test A07)
    await verify_permission(req.actor_guid, "owner")

    if req.decision not in ("released", "cancelled"):
        raise HTTPException(status_code=400, detail="Decision must be 'released' or 'cancelled'")

    lock = get_conversation_lock(conversation_id)
    async with lock:
        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT * FROM actions WHERE id = ? AND conversation_id = ?",
                (req.action_id, conversation_id)
            )
            action = await cursor.fetchone()
            if not action:
                raise HTTPException(status_code=404, detail="Action not found")
            if action["state"] != "held":
                raise HTTPException(status_code=400, detail=f"Action is not held (current state: {action['state']})")

            decision_id = str(uuid.uuid4())
            now_iso = datetime.now(timezone.utc).isoformat()

            # Record decision audit
            await db.execute(
                "INSERT INTO decisions (id, action_id, decision, decided_by, decided_at) VALUES (?, ?, ?, ?, ?)",
                (decision_id, req.action_id, req.decision, req.actor_guid, now_iso)
            )

            if req.decision == "cancelled":
                await db.execute(
                    "UPDATE actions SET state = 'cancelled', decided_at = ? WHERE id = ?",
                    (now_iso, req.action_id)
                )
                await db.commit()

                await publish_event("action.cancelled", {
                    "action_id": req.action_id,
                    "conversation_id": conversation_id,
                    "actor_guid": req.actor_guid,
                    "timestamp": now_iso
                })
                return {"action_id": req.action_id, "state": "cancelled"}

            # Decision is "released" -> Dispatch external effect
            await db.execute(
                "UPDATE actions SET state = 'dispatching', decided_at = ? WHERE id = ?",
                (now_iso, req.action_id)
            )
            await db.commit()

            try:
                payload = json.loads(action["payload"])
                outcome = await dispatch_to_engine(action["engine"], payload, action["idempotency_key"])
                await db.execute(
                    "UPDATE actions SET state = 'done', outcome = ? WHERE id = ?",
                    (json.dumps(outcome), req.action_id)
                )
                await db.commit()

                await publish_event("action.released", {
                    "action_id": req.action_id,
                    "conversation_id": conversation_id,
                    "engine": action["engine"],
                    "actor_guid": req.actor_guid,
                    "outcome": outcome,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })
                return {"action_id": req.action_id, "state": "done", "outcome": outcome}
            except Exception as e:
                await db.execute(
                    "UPDATE actions SET state = 'uncertain', outcome = ? WHERE id = ?",
                    (json.dumps({"error": str(e)}), req.action_id)
                )
                await db.commit()
                raise HTTPException(status_code=502, detail=f"Released action dispatch failed: {e}")
        finally:
            await db.close()
