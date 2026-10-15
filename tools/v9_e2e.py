"""Run local E2E (Playwright) audit runs with sanitized env and real evidence.

Espelha tools/v9_pytest.py, mas para a suíte de navegador:

- ambiente sanitizado (sem credenciais, HOME/APPDATA do proprietário);
- PLAYWRIGHT_BROWSERS_PATH aponta para os navegadores REAIS da máquina
  ANTES de LOCALAPPDATA ser redirecionado — sem isso o Playwright procura
  os binários dentro do temp e nenhum canal sobe;
- screenshots dos testes vão para docs/mission/evidence/v9-baseline/screenshots;
- log/xml/invocation no mesmo diretório de evidências dos demais runs.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'docs/mission/evidence/v9-baseline'
ALLOWED_ENV = {
    "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "PATH", "SYSTEMDRIVE",
    "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432",
    "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
}


def main() -> int:
    label, *tests = sys.argv[1:]
    if not label or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in label):
        raise ValueError('label must contain only lowercase letters, digits and hyphens')
    work = Path(tempfile.mkdtemp(prefix=f'dz23-v9-e2e-{label}-'))
    env = {k: v for k, v in os.environ.items() if k.upper() in ALLOWED_ENV}
    for key, directory in {
        'HOME': 'home', 'USERPROFILE': 'home', 'APPDATA': 'roaming',
        'LOCALAPPDATA': 'local', 'TEMP': 'tmp', 'TMP': 'tmp', 'DATA_DIR': 'data',
        'TRADINGAGENTS_RESULTS_DIR': 'results', 'TRADINGAGENTS_CACHE_DIR': 'cache',
        'XDG_CACHE_HOME': 'cache', 'XDG_CONFIG_HOME': 'config',
    }.items():
        path = work / directory
        path.mkdir(exist_ok=True)
        env[key] = str(path)
    navegadores = (os.environ.get('PLAYWRIGHT_BROWSERS_PATH')
                   or str(Path(os.environ.get('LOCALAPPDATA', '')) / 'ms-playwright'))
    if Path(navegadores).is_dir():
        env['PLAYWRIGHT_BROWSERS_PATH'] = navegadores
    screenshots = EVIDENCE / 'screenshots'
    screenshots.mkdir(parents=True, exist_ok=True)
    env.update(
        PYTHON_DOTENV_DISABLED='1', PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1',
        ALERTA_WINDOWS='false', REAL_MONEY_EXECUTION='false', MULTI_TENANT_ENABLED='false',
        TRADINGAGENTS_MEMORY_LOG_PATH=str(work / 'memory.md'),
        DZ23_E2E_EVIDENCE_DIR=str(screenshots),
    )
    xml = EVIDENCE / f'{label}.xml'
    cmd = [sys.executable, '-m', 'pytest', *tests, '-q', f'--junitxml={xml}']
    record = {
        'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'command': cmd, 'working_directory': str(ROOT), 'temporary_directory': str(work),
        'environment_names': sorted(env), 'network': 'loopback only (live_server e navegador)',
    }
    invocation = EVIDENCE / f'{label}-invocation.json'
    invocation.write_text(json.dumps(record, indent=2), encoding='utf-8')
    with (EVIDENCE / f'{label}.log').open('w', encoding='utf-8') as log:
        result = subprocess.run(cmd, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    record['exit_code'] = result.returncode
    invocation.write_text(json.dumps(record, indent=2), encoding='utf-8')
    print(f'{label}: exit={result.returncode}; log={EVIDENCE / (label + ".log")}')
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
