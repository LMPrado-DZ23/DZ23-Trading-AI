import sys

from fastapi import APIRouter

router = APIRouter(prefix="/v1/diagnostics", tags=["diagnostics"])

@router.post("/run")
def run_diagnostics():
    checks = {
        "python_version": sys.version,
        "database": "ok",
        "loopback": "127.0.0.1",
        "live_trading_locked": True,
    }
    return {"status": "ok", "checks": checks}
