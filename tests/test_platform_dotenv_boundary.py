from __future__ import annotations

import os
import subprocess
import sys


def test_package_import_never_loads_or_self_authorizes_local_dotenv(tmp_path) -> None:
    marker = "TRADINGAGENTS_IMPORT_MUST_NOT_LOAD_THIS"
    (tmp_path / ".env").write_text(
        "TRADINGAGENTS_DISABLE_DOTENV=true\n"
        f"{marker}=attacker-controlled-secret\n",
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment.pop(marker, None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import os, tradingagents; print(os.environ.get('{marker}', 'ABSENT'))",
        ],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.stdout.strip() == "ABSENT"
    assert "attacker-controlled-secret" not in result.stderr
