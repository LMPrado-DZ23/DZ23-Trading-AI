from fastapi import APIRouter

router = APIRouter(prefix="/v1/security", tags=["security"])

@router.get("/incident-center")
def get_security_status():
    return {
        "kill_switch_active": False,
        "real_money_execution": False,
        "incidents": [],
        "audit_logs": ["System running in single-owner PAPER mode."],
    }
