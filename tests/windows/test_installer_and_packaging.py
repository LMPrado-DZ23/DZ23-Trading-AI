"""
Auditoria & Homologação de Entrega Windows:
Valida a estrutura do instalador Inno Setup (identidade estável, detecção de
versão, downgrade bloqueado, encerramento seguro e backup), o construtor
PyInstaller (versão única), o workflow do GitHub Actions e os fluxos PAPER
end-to-end.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _versao_pyproject() -> str:
    import re

    texto = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    return re.search(r'^version\s*=\s*"([^"]+)"', texto, re.MULTILINE).group(1)


class TestWindowsInstallerAndWorkflow:
    def test_inno_setup_script_structure(self):
        iss_path = ROOT / "desktop" / "installer" / "setup.iss"
        assert iss_path.exists(), "setup.iss não encontrado"
        content = iss_path.read_text(encoding="utf-8")

        assert '#define AppName "DZ23 Trading AI"' in content
        assert "AppName={#AppName}" in content
        assert "DefaultDirName={localappdata}\\DZ23-Trading-AI" in content
        assert "PrivilegesRequired=lowest" in content
        # Versão vem do build (fonte única: pyproject.toml) — nada pinado à mão.
        assert "OutputBaseFilename=DZ23-Trading-AI-Setup-v{#AppVersion}" in content
        assert "AppVersion={#AppVersion}" in content
        assert "v0.4.0" not in content and "v0.5.0" not in content
        assert '#define AppExeName "DZ23-Trading-AI.exe"' in content
        assert "{userdesktop}\\{#AppName}" in content
        assert "Filename: \"{app}\\{#AppExeName}\"" in content

    def test_inno_setup_identidade_estavel_e_deteccao_de_versao(self):
        content = (ROOT / "desktop" / "installer" / "setup.iss").read_text(encoding="utf-8")

        # AppId pinado = mesma entrada do Windows que a 0.4.0 já registrou.
        assert "AppId={#AppIdConst}" in content
        assert '#define AppIdConst "DZ23 Trading AI"' in content
        # Chave de registro exata que o dono tem hoje (evidência DESKTOP-AVELL).
        assert "DZ23 Trading AI_is1" in content
        # Detecção + cenários (Atualizar / Reparar / Downgrade / Cancelar).
        assert "function PrepareToInstall" in content
        assert "VersaoNoRegistro" in content
        assert "CompararVersoes" in content
        assert "Cenario := 'ATUALIZACAO'" in content and "Cenario := 'REPARO'" in content
        assert "RESULTADO=ABORTADO_DOWNGRADE" in content
        assert "FORCEDOWNGRADE" in content

    def test_inno_setup_encerra_so_o_proprio_app_nunca_terceiros(self):
        content = (ROOT / "desktop" / "installer" / "setup.iss").read_text(encoding="utf-8")

        # Nunca encerrar via Restart Manager; fechamento só pelo --quit do app.
        assert "CloseApplications=no" in content
        assert "RestartApplications=no" in content
        # taskkill pode aparecer apenas em COMENTÁRIO ("nunca taskkill"): o
        # que se prova aqui é que nenhuma linha executável o invoca.
        executavel_iss = "\n".join(
            linha for linha in content.splitlines() if not linha.strip().startswith(";")
        ).lower()
        assert "taskkill" not in executavel_iss
        script = (ROOT / "desktop" / "installer" / "dz23_processo.ps1").read_text(encoding="utf-8")
        executavel_ps1 = "\n".join(
            linha for linha in script.splitlines() if not linha.strip().startswith("#")
        ).lower()
        assert "taskkill" not in executavel_ps1
        assert "--quit" in script
        assert "DZ23-Trading-AI-Instancia" in script  # mutex do contrato
        assert "tradingagents-agency" in script  # marcador do /healthz

    def test_inno_setup_backup_verificado_e_dry_run(self):
        content = (ROOT / "desktop" / "installer" / "setup.iss").read_text(encoding="utf-8")
        backup = (ROOT / "desktop" / "installer" / "backup_dados.ps1").read_text(encoding="utf-8")
        rollback = (ROOT / "desktop" / "installer" / "restaurar_backup.ps1").read_text(encoding="utf-8")

        assert "backup_dados.ps1" in content and "dontcopy" in content
        assert "ABORTADO_BACKUP_FALHOU" in content  # fail-closed: sem backup não instala
        assert "MANIFEST.sha256" in backup
        # Hash a hash SEM depender de cmdlet de módulo (Get-FileHash falhou no
        # ambiente filho do instalador em CI — evidência c3): .NET SHA256 puro.
        # Comentários explicativos podem NOMEAR o cmdlet; o que se prova é que
        # nenhuma linha EXECUTÁVEL o invoca.
        executavel_backup = "\n".join(
            linha for linha in backup.splitlines() if not linha.strip().startswith("#")
        )
        executavel_rollback = "\n".join(
            linha for linha in rollback.splitlines() if not linha.strip().startswith("#")
        )
        assert "Get-FileHash" not in executavel_backup
        assert "[System.Security.Cryptography.SHA256]::Create()" in executavel_backup
        assert "pre-upgrade-" in backup
        assert "MANIFEST.sha256" in rollback
        assert "Get-FileHash" not in executavel_rollback
        assert "[System.Security.Cryptography.SHA256]::Create()" in executavel_rollback
        assert "DESTINO_EXISTENTE_SEM_FORCAR" in rollback
        # Automação: status file + dry-run local sem escrever nada.
        assert "DZ23STATUS" in content and "SIMULAR" in content
        assert "RESULTADO=SIMULADO" in content

    def test_versao_unica_do_pyproject_ate_o_healthz(self):
        from tradingagents.versao import versao_do_pacote

        assert versao_do_pacote() == _versao_pyproject()
        app = (ROOT / "service" / "app.py").read_text(encoding="utf-8")
        assert '"versao": versao_do_pacote()' in app

    def test_github_release_workflow_has_inno_setup_and_artifacts(self):
        wf_path = ROOT / ".github" / "workflows" / "windows-release.yml"
        assert wf_path.exists(), "windows-release.yml não encontrado"
        raw = wf_path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw)

        assert "actions/checkout@v7" in raw
        assert "actions/setup-python@v7" in raw
        assert "actions/upload-artifact@v7" in raw
        assert "actions/download-artifact@v8" in raw

        # Release oficial nunca pode publicar artefato sem assinatura. O PFX
        # vem exclusivamente do cofre, entra no store efêmero do runner e é
        # removido mesmo se uma etapa posterior falhar.
        assert "WINDOWS_SIGNING_CERTIFICATE_BASE64" in raw
        assert "WINDOWS_SIGNING_CERTIFICATE_PASSWORD" in raw
        assert 'refs/tags/v*' in raw
        assert "Release oficial exige certificado Authenticode" in raw
        assert "Import-PfxCertificate" in raw
        assert "signtool.FullName sign" in raw
        assert "signtool.FullName verify" in raw
        assert "Remove ephemeral signing material" in raw
        assert "SIGNING_STATUS.txt" in raw

        build_steps = data["jobs"]["build"]["steps"]
        step_names = [s.get("name", "") for s in build_steps]

        assert any("Install Inno Setup" in n for n in step_names)
        assert any("Build Executable Bundle" in n for n in step_names)
        assert any("Compile Inno Setup Installer" in n for n in step_names)
        assert any("Upload build artifacts" in n for n in step_names)

        # A versão do build vem do pyproject — nunca digitada em dois lugares.
        runs = "\n".join(str(s.get("run", "")) for s in build_steps)
        assert "tomllib" in runs and "toml" in runs
        assert "/DAppVersion=" in runs or "-DAppVersion=" in runs

        # Build de branch sobe artefato para inspeção; Release só em tag v*
        # e somente depois do aceite E2E do mesmo artefato.
        publish = data["jobs"]["publish-release"]
        assert "refs/tags/v" in publish["if"]
        assert set(publish["needs"]) == {"build", "aceite-bundle"}
        release_step = next(s for s in publish["steps"] if "Create GitHub Release" in s.get("name", ""))
        files_spec = release_step["with"]["files"]
        assert "dist/DZ23-Trading-AI-Setup-v" in files_spec and ".exe" in files_spec
        assert "dist/DZ23-Trading-AI-windows-x64.zip" in files_spec
        assert "dist/SHA256SUMS.txt" in files_spec

    def test_workflow_da_matriz_do_instalador_existe_e_roda_a_matriz(self):
        wf_path = ROOT / ".github" / "workflows" / "installer-matrix.yml"
        assert wf_path.exists(), "installer-matrix.yml não encontrado"
        raw = wf_path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw)
        assert "actions/checkout@v7" in raw
        assert "actions/setup-python@v7" in raw
        assert "actions/upload-artifact@v7" in raw
        job = data["jobs"]["matriz"]
        runs = "\n".join(str(s.get("run", "")) for s in job["steps"])
        assert "matriz_instalador_win.py" in runs
        assert "ISCC.exe" in runs
        # O bundle ANTIGO (0.4.0) é construído do commit congelado — a matriz
        # prova o upgrade real 0.4.0→novo sem depender de artefato expirado.
        assert "30b8683" in runs
        assert job["runs-on"] == "windows-latest"

    def test_build_exe_includes_all_modules_and_resources(self):
        from desktop.packaging.build_exe import ROOT as build_root
        assert build_root == ROOT
        build_exe_script = (ROOT / "desktop" / "packaging" / "build_exe.py").read_text(encoding="utf-8")

        assert "--collect-all=service" in build_exe_script
        assert "--collect-all=tradingagents" in build_exe_script
        assert "--hidden-import=uvicorn.logging" in build_exe_script
        # Versão single-source: recurso de versão do exe + metadado embarcado
        # (é o metadado que o --version/healthz leem no bundle).
        assert "--version-file=" in build_exe_script
        assert "--copy-metadata=tradingagents" in build_exe_script
        assert "ler_versao_pyproject" in build_exe_script


class TestSettingsAndPaperHomologation:
    def test_live_trading_hard_locked_fail_closed(self):
        import os

        assert os.environ.get("REAL_MONEY_EXECUTION", "false").lower() == "false"

        # V9-SEC-06: o stub /v1/diagnostics/run (resposta fixa, sem auth) foi
        # eliminado; o bloqueio é afirmado na fonte real — o gate fail-closed
        # do próprio serviço.
        import service.app as m

        assert m.MESA_LIVE_LIBERADA is False

    def test_ai_key_masking_in_settings(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        import service.app as m

        # Verify secret keys are declared for masking
        assert "OPENAI_API_KEY" in m.SEGREDOS_DECLARADOS
        assert "ANTHROPIC_API_KEY" in m.SEGREDOS_DECLARADOS
        assert "DEEPSEEK_API_KEY" in m.SEGREDOS_DECLARADOS
