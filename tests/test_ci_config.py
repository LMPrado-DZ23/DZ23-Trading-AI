"""O pipeline precisa ser barato o bastante para rodar sempre — CI que
estoura o limite de minutos é CI que não protege ninguém."""
from pathlib import Path

import yaml

_BRUTO = yaml.safe_load((Path(__file__).resolve().parents[1] /
                         ".github/workflows/ci.yml").read_text(encoding="utf-8"))
# Armadilha do YAML: a chave `on:` é lida como o booleano True.
CI = dict(_BRUTO)
CI["on"] = _BRUTO.get("on") or _BRUTO.get(True)
RAW = (Path(__file__).resolve().parents[1] /
       ".github/workflows/ci.yml").read_text(encoding="utf-8")


class TestCustoDoPipeline:
    def test_matrix_is_conditional_not_always_four_versions(self):
        matriz = CI["jobs"]["test"]["strategy"]["matrix"]["python-version"]
        assert isinstance(matriz, str) and "github.ref" in matriz, (
            "a matriz completa deve rodar em main/PR, não a cada push")

    def test_dependency_cache_is_enabled_everywhere(self):
        assert RAW.count("astral-sh/setup-uv@v10.3.0") >= 4
        assert RAW.count("uv sync --locked") >= 4

    def test_actions_nao_usam_runtime_node20_depreciado(self):
        assert "actions/checkout@v4" not in RAW
        assert "actions/setup-python@v5" not in RAW
        assert "actions/upload-artifact@v4" not in RAW
        assert "astral-sh/setup-uv@v5" not in RAW
        assert "gitleaks/gitleaks-action@v2" not in RAW

    def test_documentation_changes_do_not_burn_minutes(self):
        ignorados = CI["on"]["push"]["paths-ignore"]
        assert "**/*.md" in ignorados and "docs/**" in ignorados

    def test_runs_are_cancelled_when_superseded(self):
        assert CI["concurrency"]["cancel-in-progress"] is True

    def test_push_e_pr_da_mesma_branch_compartilham_o_grupo(self):
        grupo = CI["concurrency"]["group"]
        assert "github.head_ref" in grupo and "github.ref_name" in grupo
        assert "github.event.pull_request.head.repo.full_name" in grupo
        assert "github.repository" in grupo
        assert "github.ref }}" not in grupo, (
            "github.ref separa push e pull_request e duplica o pipeline")

    def test_working_branch_is_covered(self):
        branches = CI["on"]["push"]["branches"]
        assert "main" in branches and "unificacao" in branches

    def test_product_extras_are_installed(self):
        assert RAW.count("--extra desk --extra dev") >= 2, (
            "o CI precisa instalar o produto completo com os extras travados"
        )
