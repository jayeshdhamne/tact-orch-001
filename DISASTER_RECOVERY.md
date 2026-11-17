# Disaster Recovery & State Restoration (TACT-ORCH-001)

## 1. Overview
This runbook defines the exact procedures to backup, restore, and verify state for the **TACT-ORCH-001** orchestration stack in case of hardware failure, corruption, or host migration.

---

## 2. Persistent Storage Architecture
State is maintained across 6 named Docker volumes:
- `gate-data`: SQLite database (`/data/gate.db` in WAL mode) storing conversation modes, held actions, and idempotency cache.
- `nats-data`: JetStream file store (`/data/jetstream`) preserving authoritative immutable audit log events.
- `n8n_data`: n8n SQLite DB and workflow definitions.
- `pg-data`: PostgreSQL 16 database storing Windmill workspaces, users, scripts, and job histories.
- `calnode-data`: Calnode embedded SQLite scheduler database (`/data/calnode.db`).
- `windmill-data`: Windmill script caches and execution artifacts.

---

## 3. Automated Backup Procedure

To take a complete snapshot of all running state:

```bash
#!/usr/bin/env bash
BACKUP_DIR="./backups/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"

# 1. Backup SQLite Gate Database (using SQLite online backup or copy)
docker exec safi-gate sqlite3 /data/gate.db ".backup '/data/gate_backup.db'"
docker cp safi-gate:/data/gate_backup.db "$BACKUP_DIR/gate.db"

# 2. Dump Postgres (Windmill)
docker exec safi-postgres pg_dump -U windmill -d windmill > "$BACKUP_DIR/windmill.sql"

# 3. Export n8n Workflows
docker exec safi-n8n n8n export:workflow --all > "$BACKUP_DIR/n8n_workflows.json"

# 4. Copy Calnode SQLite Database
docker cp safi-calnode:/data/calnode.db "$BACKUP_DIR/calnode.db"

echo "Backup completed successfully to $BACKUP_DIR"
```

---

## 4. Full System Restoration Procedure

If the host machine is re-imaged or files are lost:

### Step 1: Clone Repository & Restore Environment
```bash
git clone <repo-url>
cd "New folder"
cp .env.example .env
# Edit .env with your secrets
```

### Step 2: Restore Volumes & Databases
```bash
# Start storage containers first
docker compose up -d postgres nats n8n calnode

# Restore Postgres
docker exec -i safi-postgres psql -U windmill -d windmill < "./backups/<timestamp>/windmill.sql"

# Restore Calnode DB
docker cp "./backups/<timestamp>/calnode.db" safi-calnode:/data/calnode.db
docker restart safi-calnode

# Restore Gate SQLite DB
docker cp "./backups/<timestamp>/gate.db" safi-gate:/data/gate.db
docker restart safi-gate
```

### Step 3: Start Remaining Services & Verify Convergence
```bash
./runbook.sh up
```

### Step 4: Run Verification Tests
```bash
python scripts/test_evidence_suite.py
```
If all 6 tests output `PASS`, the restore is verified complete.
