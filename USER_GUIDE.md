# SafiChat Orchestration OS — User Guide (TACT-ORCH-001)

## 1. Overview
SafiChat Orchestration OS allows a business owner to manage multi-engine business operations (**n8n**, **Calnode**, and **Windmill**) from a single interface while guaranteeing that external actions can be paused, inspected, and approved in real time.

---

## 2. Accessing the System
Once started via `./runbook.sh up`, open your browser to:
- **SafiChat Console:** [http://127.0.0.1:8080](http://127.0.0.1:8080)
- **Zero-Leak Mailpit Inbox:** [http://127.0.0.1:8025](http://127.0.0.1:8025)
- **Calnode Admin:** [http://127.0.0.1:3000/admin/](http://127.0.0.1:3000/admin/)
- **Windmill UI:** [http://127.0.0.1:8000](http://127.0.0.1:8000)

---

## 3. Core Concepts & Navigation

### A. Conversation Rooms
On the **Left Sidebar**, you can switch between active customer conversations:
1. `conv-customer-a-enquiry` (Customer A – Website Enquiry)
2. `conv-customer-b-pricing` (Customer B – Pricing Question)
3. `conv-customer-a-demo` (Customer A – Demo Request)

When you select a room, the center workspace immediately updates with the customer's incoming lead message and LiveKit room token status.

### B. Mode Control & Barge In
In the room header, you will see the active mode badge:
- 🟢 **UNGATED (Auto-Pilot):** Outgoing actions execute immediately when triggered.
- 🔴 **GATED (Approval Required):** Outgoing actions are held in an approval queue until explicitly released by the business owner.

**To Barge In:** Click the red **"Barge In (Gate)"** button. The conversation switches to Gated mode instantly, while other active conversations continue running uninterrupted. To resume auto-pilot, click **"Return to Ungated"**.

### C. Triggering Engine Dispatches
In the center workspace under **Engine Dispatch Hub**, click any of the 3 engine cards:
1. **1. Marketing Lead (n8n):** Sends customer inquiry to the automated n8n webhook.
2. **2. Book Meeting (Calnode):** Queries available appointment slots and reserves calendar time.
3. **3. Operations Task (Windmill):** Runs the `u/admin/assign_task` Python script to assign a team member.
- **Run Complete Customer Flow:** Automatically executes all 3 engines in sequence (n8n → Calnode → Windmill).

### D. Approving or Cancelling Held Actions
When a room is **GATED**:
1. Any triggered action appears on the **Right Sidebar** under **"Held Actions"**.
2. You can inspect the target engine, Action ID, and exact JSON payload.
3. Click **"Release"** to dispatch the action to the real backend, or **"Cancel"** to discard it.
4. Duplicate clicks on the same action are safely rejected by the Gate's idempotency engine.

### E. Actor Identity & RBAC (Role-Based Access)
Use the **Actor Identity** dropdown in the left panel to test security policies:
- `u-owner-guid` (Authorized Owner): Full control to dispatch, switch modes, and release held actions.
- `u-viewer-guid` (Read Only): Dispatches and decisions are blocked with `HTTP 403 Forbidden`.
- `u-revoked-guid` (Revoked): Access is rejected with `HTTP 403: Unauthorised or permission revoked`.

### F. Checking Outbound Emails
All outgoing emails triggered by bookings or marketing follow-ups are safely captured in **Mailpit** ([http://127.0.0.1:8025](http://127.0.0.1:8025)). Real customer inboxes are never emailed.
