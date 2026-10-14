"""AC-1/A1 — rastreabilidade do manifest do backtest (débito FQ-3 #1).

O campo `code_revision` tem de carregar a REVISÃO DE CÓDIGO que produziu o
veredito: git SHA da árvore (com sufixo `-dirty` quando houver modificação
rastreada) — nunca o identificador da janela, que ganha campo próprio
(`janela`). Fora de um repositório git (ex.: bundle instalado) o valor é o
aviso honesto `working-tree`, o mesmo de antes — nada inventado.
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import tradingagents.research.backtest as bt
from tradingagents.research import (
    BacktestConfig,
    Bar,
    revisao_do_codigo,
    run_backtest,
    run_walk_forward,
    windows,
)


def bars(n=6):
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    return tuple(Bar(start + timedelta(days=i), start + timedelta(days=i),
                     Decimal(100 + i), Decimal(101 + i), Decimal(99 + i),
                     Decimal(100 + i), Decimal(10)) for i in range(n))


def _git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True,
                          timeout=10).stdout.strip()


class SemGit:
    """Simula máquina sem git (ex.: bundle instalado): toda chamada falha."""

    def run(self, *a, **kw):
        raise OSError("sem git nesta máquina")


class TestRevisaoDoCodigo:
    def test_no_repo_de_devolve_o_commit_real(self):
        sha = _git("rev-parse", "HEAD")
        assert len(sha) == 40, sha
        valor = revisao_do_codigo()
        assert valor in (sha, f"{sha}-dirty"), valor
        assert "walk-forward" not in valor, (
            "identificador de janela não é revisão de código")

    def test_arvore_suja_e_declarada_nao_escondida(self):
        sha = _git("rev-parse", "HEAD")
        sujo = subprocess.run(["git", "diff", "--quiet"],
                              capture_output=True, timeout=10).returncode == 1
        valor = revisao_do_codigo()
        assert valor.endswith("-dirty") is sujo, valor
        assert valor.startswith(sha)

    def test_sem_git_e_o_aviso_honesto_working_tree(self, monkeypatch):
        monkeypatch.setattr(bt, "subprocess", SemGit())
        assert revisao_do_codigo() == "working-tree"


class TestManifestDoWalkForward:
    def test_revisao_real_e_janela_separada_por_dobra(self):
        b = bars(40)
        resultados = run_walk_forward(b, train_size=20, test_size=5,
                                      signal=lambda history: 0,
                                      config=BacktestConfig())
        janelas = windows(40, 20, 5)
        revisao = revisao_do_codigo()
        assert len(resultados) == len(janelas) == 4
        for r, j in zip(resultados, janelas, strict=True):
            # o campo que promete CÓDIGO entrega o commit que rodou,
            # o mesmo em todas as dobras (o código não muda entre dobras)
            assert r.manifest["code_revision"] == revisao, r.manifest
            # a janela é a janela: campo próprio, distinto por dobra
            assert r.manifest["janela"] == f"{j.train_start}:{j.train_end}"
        assert len({r.manifest["janela"] for r in resultados}) == 4
        assert len({r.dataset_hash for r in resultados}) == 4

    def test_sem_git_o_manifest_avisa_working_tree(self, monkeypatch):
        monkeypatch.setattr(bt, "subprocess", SemGit())
        resultados = run_walk_forward(bars(40), 20, 5, lambda h: 0,
                                      BacktestConfig())
        assert resultados
        for r in resultados:
            assert r.manifest["code_revision"] == "working-tree"
            assert "janela" in r.manifest

    def test_run_backtest_direto_mantem_o_aviso_padrao(self):
        # Chamada direta sem revisão continua "working-tree": resolver git
        # aqui tornaria o manifest dependente do ambiente de quem chama;
        # quem resolve é o caminho de agregação (walk-forward).
        r = run_backtest(bars(), lambda h: 0, BacktestConfig())
        assert r.manifest["code_revision"] == "working-tree"
