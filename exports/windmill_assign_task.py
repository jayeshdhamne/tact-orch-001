# ==============================================================================
# Windmill Worker Script: u/admin/assign_task
# TACT-ORCH-001 Operations Task Assignment
# ==============================================================================

import datetime

def main(booking_id: str, assignee: str = "support@safi.internal", task_description: str = "Follow up on customer meeting"):
    now = datetime.datetime.utcnow().isoformat()
    return {
        "status": "ASSIGNED",
        "booking_id": booking_id,
        "assignee": assignee,
        "task_description": task_description,
        "assigned_at": now,
        "job_result": f"Task successfully assigned to {assignee} for booking {booking_id}"
    }
