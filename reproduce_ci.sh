#!/usr/bin/env bash
set -euo pipefail

# reproduce_ci.sh
# Reproduz localmente os passos principais do workflow CI para diagnóstico e
# tentativa automática de correção de falhas comuns (dependências ausentes,
# variáveis de ambiente). Cria logs em logs/ para análise.

ROOT=$(cd "$(dirname "$0")" && pwd)
LOGDIR="$ROOT/logs"
VENV="$ROOT/.venv"
PYTHON=${PYTHON:-python3}
ACCESS_PASSWORD=${ACCESS_PASSWORD:-test-password}
PERSONAL_TRADING_LAB_ENABLED=${PERSONAL_TRADING_LAB_ENABLED:-false}

mkdir -p "$LOGDIR"

echo "[1/8] Creating virtualenv at $VENV" | tee "$LOGDIR/run.log"
$PYTHON -m venv "$VENV"
# shellcheck disable=SC1090
source "$VENV/bin/activate"

echo "[2/8] Upgrading pip and wheel" | tee -a "$LOGDIR/run.log"
pip install -U pip wheel setuptools >/dev/null

echo "[3/8] Installing package with dev and desk extras (may take a while)" | tee -a "$LOGDIR/run.log"
# Install editable package with extras to simulate CI 'uv sync --locked --extra desk --extra dev'
pip install -e ".[dev,desk]" 2>&1 | tee "$LOGDIR/pip-install.log"

# export envs used by tests and smoke scripts
export ACCESS_PASSWORD
export PERSONAL_TRADING_LAB_ENABLED
export OPENAI_BASE_URL=""
export OPENAI_API_BASE=""

echo "[4/8] Running pytest" | tee -a "$LOGDIR/run.log"
pytest -q 2>&1 | tee "$LOGDIR/pytest.log" || true

# quick analyzer to search for ImportError/ModuleNotFoundError
if grep -E "ModuleNotFoundError|ImportError" -n "$LOGDIR/pytest.log" >/dev/null 2>&1; then
  echo "[!] Detected ImportError/ModuleNotFoundError in pytest output" | tee -a "$LOGDIR/run.log"
  echo "[i] Showing unique missing modules:" | tee -a "$LOGDIR/run.log"
  grep -E "ModuleNotFoundError|ImportError" -n "$LOGDIR/pytest.log" | sed -E "s/.*(ModuleNotFoundError|ImportError): //" | sort -u | tee "$LOGDIR/missing_deps.txt"
  cat "$LOGDIR/missing_deps.txt" | while read -r mod; do
    echo "[i] Attempting to pip install $mod" | tee -a "$LOGDIR/run.log"
    pip install "$mod" 2>&1 | tee -a "$LOGDIR/pip-autoinstall.log" || echo "failed to pip install $mod" | tee -a "$LOGDIR/run.log"
  done
  echo "[i] Re-running pytest after attempting to install missing dependencies" | tee -a "$LOGDIR/run.log"
  pytest -q 2>&1 | tee "$LOGDIR/pytest.rerun.log" || true
fi

echo "[5/8] Compile modules (byte-compile)" | tee -a "$LOGDIR/run.log"
python -m compileall -q tradingagents service scripts 2>&1 | tee "$LOGDIR/compileall.log" || true

echo "[6/8] Run smoke scripts" | tee -a "$LOGDIR/run.log"
# smoke scripts expect ACCESS_PASSWORD to be set and may assume other envs
export ACCESS_PASSWORD

python scripts/smoke_agency_api.py 2>&1 | tee "$LOGDIR/smoke_agency_api.log" || true
python scripts/smoke_multitenant.py 2>&1 | tee "$LOGDIR/smoke_multitenant.log" || true

echo "[7/8] Summarize logs" | tee -a "$LOGDIR/run.log"
for f in pytest.log pytest.rerun.log compileall.log smoke_agency_api.log smoke_multitenant.log pip-install.log pip-autoinstall.log; do
  if [ -f "$LOGDIR/$f" ]; then
    echo "---- $f (last 50 lines) ----" | tee -a "$LOGDIR/run.log"
    tail -n 50 "$LOGDIR/$f" | sed 's/^/    /' | tee -a "$LOGDIR/run.log"
  fi
done

echo "[8/8] Done. Collected logs in $LOGDIR" | tee -a "$LOGDIR/run.log"

echo "Next steps: inspect $LOGDIR/pytest*.log and smoke_*.log for failures; paste them to the ticket or here for analysis."
