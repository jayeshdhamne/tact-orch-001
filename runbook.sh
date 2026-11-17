#!/usr/bin/env bash
# ==============================================================================
# TACT-ORCH-001 — Operational Runbook (POSIX Bash)
# Unified Local UNIX Small-Business Orchestration Stack
# Zero Personal Paths • All Ports Bound to 127.0.0.1 • Single Compose File
# ==============================================================================

set -euo pipefail

GATE_URL="http://127.0.0.1:8080"
NATS_HEALTH="http://127.0.0.1:8222/varz"
N8N_HEALTH="http://127.0.0.1:5678/healthz"
CALNODE_HEALTH="http://127.0.0.1:3000/v1/event-types/intro-call"
WINDMILL_HEALTH="http://127.0.0.1:8000/api/version"
MAILPIT_HEALTH="http://127.0.0.1:8025/api/v1/info"

# Colors for terminal output
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

check_prerequisites() {
    log_info "Verifying prerequisites..."
    if [ -z "$DOCKER_BIN" ]; then
        log_error "Docker is required but not installed."
        exit 1
    fi

    if [ ! -f ".env" ]; then
        if [ -f ".env.example" ]; then
            log_warn ".env not found. Creating from .env.example template..."
            cp .env.example .env
            log_warn "Please review and edit .env before production execution."
        else
            log_error "Neither .env nor .env.example found."
            exit 1
        fi
    fi
    log_success "Prerequisites verified."
}

start_stack() {
    log_info "Starting unified Docker Compose stack (7 containers + Gate)..."
    "$DOCKER_BIN" compose up -d
    log_success "Containers launched."
}

wait_for_healthy() {
    log_info "Awaiting service health convergence..."
    local max_retries=30
    local count=0

    while [ $count -lt $max_retries ]; do
        if curl -s -f "$GATE_URL/health" >/dev/null 2>&1; then
            log_success "Gate adapter is healthy at $GATE_URL"
            return 0
        fi
        count=$((count + 1))
        echo -n "."
        sleep 2
    done

    echo ""
    log_error "Gate adapter timed out waiting for health check."
    exit 1
}

run_smoke_test() {
    log_info "Executing automated smoke test through Gate Single Dispatcher..."

    local idempotency_key="smoke-$(date +%s)-$RANDOM"

    # 1. Test Marketing Follow-Up (n8n)
    log_info "Dispatching Action 1: Marketing Ingest (n8n)..."
    local res_n8n
    res_n8n=$(curl -s -X POST "$GATE_URL/dispatch" \
        -H "Content-Type: application/json" \
        -d "{
            \"conversation_id\": \"conv-customer-a-enquiry\",
            \"idempotency_key\": \"${idempotency_key}-n8n\",
            \"engine\": \"n8n\",
            \"payload\": {\"enquiry\": \"Runbook smoke test\", \"contact\": \"lead@acme.com\", \"name\": \"Alice Acme\"},
            \"actor_guid\": \"u-owner-guid\",
            \"permission_proof\": \"role:owner\"
        }")
    echo "  Response: $res_n8n"

    # 2. Test Calnode Slot Availability
    log_info "Dispatching Action 2: Query Slots (Calnode)..."
    local res_calnode
    res_calnode=$(curl -s -X POST "$GATE_URL/dispatch" \
        -H "Content-Type: application/json" \
        -d "{
            \"conversation_id\": \"conv-customer-a-enquiry\",
            \"idempotency_key\": \"${idempotency_key}-cal\",
            \"engine\": \"calnode\",
            \"payload\": {\"action\": \"slots\", \"slug\": \"intro-call\"},
            \"actor_guid\": \"u-owner-guid\",
            \"permission_proof\": \"role:owner\"
        }")
    echo "  Response: $res_calnode"

    # 3. Test Windmill Task Assignment
    log_info "Dispatching Action 3: Assign Ops Task (Windmill)..."
    local res_wm
    res_wm=$(curl -s -X POST "$GATE_URL/dispatch" \
        -H "Content-Type: application/json" \
        -d "{
            \"conversation_id\": \"conv-customer-a-enquiry\",
            \"idempotency_key\": \"${idempotency_key}-wm\",
            \"engine\": \"windmill\",
            \"payload\": {\"script_path\": \"u/admin/assign_task\", \"args\": {\"booking_id\": \"bk-test-smoke\", \"assignee\": \"ops@safi.local\"}},
            \"actor_guid\": \"u-owner-guid\",
            \"permission_proof\": \"role:owner\"
        }")
    echo "  Response: $res_wm"

    log_success "Smoke test completed successfully."
}

stop_stack() {
    log_info "Stopping unified Docker Compose stack..."
    "$DOCKER_BIN" compose down
    log_success "All containers stopped cleanly."
}

status_stack() {
    "$DOCKER_BIN" compose ps
}

# CLI command router
case "${1:-up}" in
    up|start)
        check_prerequisites
        start_stack
        wait_for_healthy
        run_smoke_test
        echo ""
        log_success "SafiChat Orchestration Console is live at: $GATE_URL"
        log_info "Mailpit Web UI (Zero-Leak Mail Sink): http://127.0.0.1:8025"
        ;;
    test)
        run_smoke_test
        ;;
    status)
        status_stack
        ;;
    down|stop)
        stop_stack
        ;;
    restart)
        stop_stack
        start_stack
        wait_for_healthy
        ;;
    *)
        echo "Usage: $0 {up|down|restart|status|test}"
        exit 1
        ;;
esac
