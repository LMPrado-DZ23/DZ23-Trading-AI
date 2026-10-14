"""
Auditoria & Homologação de Entrega Windows:
Valida a estrutura do instalador Inno Setup, o construtor PyInstaller,
o workflow do GitHub Actions e os fluxos PAPER end-to-end.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


class TestWindowsInstallerAndWorkflow:
    def test_inno_setup_script_structure(self):
        iss_path = ROOT / "desktop" / "installer" / "setup.iss"
        assert iss_path.exists(), "setup.iss não encontrado"
        content = iss_path.read_text(encoding="utf-8")

        assert "AppName=DZ23 Trading AI" in content
        assert "DefaultDirName={localappdata}\\DZ23-Trading-AI" in content
        assert "PrivilegesRequired=lowest" in content
        assert "OutputBaseFilename=DZ23-Trading-AI-Setup-v0.4.0" in content
        assert "{userdesktop}\\DZ23 Trading AI" in content
        assert "Filename: \"{app}\\DZ23-Trading-AI.exe\"" in content

    def test_github_release_workflow_has_inno_setup_and_artifacts(self):
        wf_path = ROOT / ".github" / "workflows" / "windows-release.yml"
        assert wf_path.exists(), "windows-release.yml não encontrado"
        data = yaml.safe_load(wf_path.read_text(encoding="utf-8"))

        build_steps = data["jobs"]["build"]["steps"]
        step_names = [s.get("name", "") for s in build_steps]

        assert any("Install Inno Setup" in n for n in step_names)
        assert any("Build Executable Bundle" in n for n in step_names)
        assert any("Compile Inno Setup Installer" in n for n in step_names)
        assert any("Upload build artifacts" in n for n in step_names)

        # Build de branch sobe artefato para inspeção; Release só em tag v*
        # (publicação exige gatilho humano explícito).
        publish = data["jobs"]["publish-release"]
        assert "refs/tags/v" in publish["if"]
        release_step = next(s for s in publish["steps"] if "Create GitHub Release" in s.get("name", ""))
        files_spec = release_step["with"]["files"]
        assert "dist/DZ23-Trading-AI-Setup-v0.4.0.exe" in files_spec
        assert "dist/DZ23-Trading-AI-windows-x64.zip" in files_spec
        assert "dist/SHA256SUMS.txt" in files_spec

    def test_build_exe_includes_all_modules_and_resources(self):
        from desktop.packaging.build_exe import ROOT as build_root
        assert build_root == ROOT
        build_exe_script = (ROOT / "desktop" / "packaging" / "build_exe.py").read_text(encoding="utf-8")

        assert "--collect-all=service" in build_exe_script
        assert "--collect-all=tradingagents" in build_exe_script
        assert "--hidden-import=uvicorn.logging" in build_exe_script


class TestSettingsAndPaperHomologation:
    def test_live_trading_hard_locked_fail_closed(self):
        import os

        from service.api.diagnostics import run_diagnostics
        assert os.environ.get("REAL_MONEY_EXECUTION", "false").lower() == "false"

        # Verify diagnostics API reports live trading as locked
        diag_res = run_diagnostics()
        assert diag_res["checks"]["live_trading_locked"] is True

    def test_ai_key_masking_in_settings(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        import service.app as m

        # Verify secret keys are declared for masking
        assert "OPENAI_API_KEY" in m.SEGREDOS_DECLARADOS
        assert "ANTHROPIC_API_KEY" in m.SEGREDOS_DECLARADOS
        assert "DEEPSEEK_API_KEY" in m.SEGREDOS_DECLARADOS
