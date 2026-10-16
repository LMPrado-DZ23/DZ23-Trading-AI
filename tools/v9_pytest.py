"""Run local audit tests without inherited credentials or owner data paths.

Set DZ23_V9_EVIDENCE_DIR (relative to repo root) to redirect the evidence trio
away from the frozen v9-baseline directory.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_ENV = {
    "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "PATH", "SYSTEMDRIVE",
    "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432",
    "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
}
CHILD = """
import ipaddress
import sys

def guard(event, args):
    if event in ('winreg.CreateKey', 'winreg.DeleteKey', 'winreg.DeleteValue',
                 'winreg.SetValue', 'winreg.SaveKey', 'winreg.LoadKey'):
        raise RuntimeError('audit forbids registry writes')
    if event in ('socket.connect', 'socket.getaddrinfo'):
        host = args[1][0] if event == 'socket.connect' else args[0]
        if host == 'localhost':
            return
        try:
            if ipaddress.ip_address(host).is_loopback:
                return
        except ValueError:
            pass
        raise RuntimeError('audit permits loopback network only')
sys.addaudithook(guard)
import pytest
raise SystemExit(pytest.main(sys.argv[1:]))
"""


def main() -> int:
    label, *tests = sys.argv[1:]
    if not label or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in label):
        raise ValueError('label must contain only lowercase letters, digits and hyphens')
    work = Path(tempfile.mkdtemp(prefix=f'dz23-v9-{label}-'))
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
    env.update(
        PYTHON_DOTENV_DISABLED='1', PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1',
        ALERTA_WINDOWS='false', REAL_MONEY_EXECUTION='false', MULTI_TENANT_ENABLED='false',
        TRADINGAGENTS_MEMORY_LOG_PATH=str(work / 'memory.md'),
    )
    rel = os.environ.get('DZ23_V9_EVIDENCE_DIR')
    evidence = (ROOT / rel) if rel else (ROOT / 'docs/mission/evidence/v9-baseline')
    evidence.mkdir(parents=True, exist_ok=True)
    xml = evidence / f'{label}.xml'
    # O arquivo de teste pode viver fora do repositório (os testes de
    # isolamento usam tmp_path). Sem raiz explícita, pytest pode escolher o
    # ancestral comum do arquivo temporário como rootdir e coletar outras
    # pastas transitórias do Windows, inclusive artefatos Playwright que
    # desaparecem em paralelo. A raiz auditada é sempre este repositório.
    cmd = [sys.executable, '-B', '-c', CHILD, *tests, '-q',
           f'--rootdir={ROOT}', f'--junitxml={xml}']
    record = {
        'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'command': cmd, 'working_directory': str(ROOT), 'temporary_directory': str(work),
        'environment_names': sorted(env), 'network': 'loopback only',
    }
    invocation = evidence / f'{label}-invocation.json'
    invocation.write_text(json.dumps(record, indent=2), encoding='utf-8')
    with (evidence / f'{label}.log').open('w', encoding='utf-8') as log:
        result = subprocess.run(cmd, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    record['exit_code'] = result.returncode
    invocation.write_text(json.dumps(record, indent=2), encoding='utf-8')
    print(f'{label}: exit={result.returncode}; log={evidence / (label + ".log")}')
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
