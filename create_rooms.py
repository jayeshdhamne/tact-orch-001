"""
TACT-ORCH-001 — Create the 3 LiveKit Rooms
Run: python create_rooms.py
"""

import os
import json
import urllib.request
import urllib.error
import time
import hmac
import hashlib
import base64

# ─── FILL THESE IN ────────────────────────────────────────────
LIVEKIT_URL    = "https://13cbrufngvm.livekit.cloud"   # your project HTTP URL
LIVEKIT_API_KEY    = "YOUR_API_KEY_HERE"               # Settings → Keys
LIVEKIT_API_SECRET = "YOUR_API_SECRET_HERE"            # Settings → Keys
# ──────────────────────────────────────────────────────────────

ROOMS = [
    {
        "name": "conv-customer-a-enquiry",
        "metadata": json.dumps({
            "subject_line": "Customer A – Website Enquiry",
            "meeting_port_context": {"type": "enquiry", "owner": "u-owner-guid"}
        })
    },
    {
        "name": "conv-customer-b-pricing",
        "metadata": json.dumps({
            "subject_line": "Customer B – Pricing Question",
            "meeting_port_context": {"type": "pricing", "owner": "u-owner-guid"}
        })
    },
    {
        "name": "conv-customer-a-demo",
        "metadata": json.dumps({
            "subject_line": "Customer A – Demo Request",
            "meeting_port_context": {"type": "demo", "owner": "u-owner-guid"}
        })
    },
]


def make_jwt(api_key, api_secret):
    """Build a minimal LiveKit admin JWT (no extra libraries needed)."""
    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "HS256", "typ": "JWT"}).encode()
    ).rstrip(b"=").decode()

    now = int(time.time())
    payload = base64.urlsafe_b64encode(
        json.dumps({
            "iss": api_key,
            "exp": now + 300,       # valid for 5 minutes
            "nbf": now,
            "video": {"roomCreate": True, "roomList": True}
        }).encode()
    ).rstrip(b"=").decode()

    sig_input = f"{header}.{payload}".encode()
    sig = hmac.new(api_secret.encode(), sig_input, hashlib.sha256).digest()
    signature = base64.urlsafe_b64encode(sig).rstrip(b"=").decode()
    return f"{header}.{payload}.{signature}"


def create_room(token, room_name, metadata):
    url = f"{LIVEKIT_URL.rstrip('/')}/twirp/livekit.RoomService/CreateRoom"
    body = json.dumps({"name": room_name, "metadata": metadata}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read())
            print(f"  ✅ Created : {result.get('name')}  SID: {result.get('sid')}")
            return result
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"  ❌ Failed  : {room_name} — {e.code} {body}")
        return None


def list_rooms(token):
    url = f"{LIVEKIT_URL.rstrip('/')}/twirp/livekit.RoomService/ListRooms"
    req = urllib.request.Request(
        url,
        data=b"{}",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
        },
        method="POST"
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read()).get("rooms", [])


if __name__ == "__main__":
    if "YOUR_API_KEY_HERE" in LIVEKIT_API_KEY:
        print("❌ Please fill in LIVEKIT_API_KEY and LIVEKIT_API_SECRET before running.")
        raise SystemExit(1)

    token = make_jwt(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)

    print("\n🚀 Creating rooms...\n")
    created = []
    for r in ROOMS:
        result = create_room(token, r["name"], r["metadata"])
        if result:
            created.append(result)

    print(f"\n📋 Verification — rooms now in project:\n")
    token2 = make_jwt(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
    for room in list_rooms(token2):
        marker = "✅" if room["name"] in [r["name"] for r in ROOMS] else "  "
        print(f"  {marker} {room['name']}  (SID: {room.get('sid','?')})")

    print("\n✅ Done. Copy the SIDs above into your .env file.\n")
