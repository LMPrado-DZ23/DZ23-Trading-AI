"""Escrita atômica sob concorrência.

Bug real que o CI pegou (e meus testes não): a gravação usava um temporário
de NOME FIXO. Com o laço gravando a cada ciclo, duas escritas disputavam o
mesmo .tmp — a primeira o consumia no replace e a segunda estourava
FileNotFoundError. Passava no Python 3.13 e falhava no 3.10/3.11/3.12: puro
acaso de temporização.

V9-TEST-03: no Windows o `os.replace` do escritor nega aberturas
concorrentes com PermissionError transitório (medido em
docs/mission/evidence/v9-baseline/v9-test03-probe.log). A prova de leitura
espera e re-tenta negação transitória, só reprova negação persistente — e
nenhuma exceção pode encerrar a thread leitora em silêncio (era o defeito:
a thread morria, o warning ficava escondido e "8 passed" virava prova oca).

Estes testes não dependem de sorte: forçam a concorrência.
"""
from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tradingagents.desk.storage import ArmazemDeEstado, escrever_json_atomico

_MAX_NEGACOES = 100
_PAUSA_LEITURA_S = 0.001


def _vigiar_leitura(alvo: Path, parar: threading.Event,
                    problemas: list[str], transitorios: list[str]) -> None:
    """Lê `alvo` até `parar`; toda anomalia vira registro — nunca morte muda.

    No Windows o `os.replace` do escritor nega aberturas concorrentes com
    PermissionError transitório (medido em v9-test03-probe.log). Leitura
    espaçada a 1 ms dá folga ao orçamento de retentativas do escritor;
    negação que insiste além de `_MAX_NEGACOES` leituras seguidas vira
    problema visível em vez de sumir.
    """
    negacoes = 0
    while not parar.is_set():
        try:
            json.loads(alvo.read_text(encoding="utf-8"))
            negacoes = 0
        except ValueError:
            problemas.append("json incompleto observado")
        except FileNotFoundError:
            problemas.append("arquivo sumiu durante a troca")
        except PermissionError as exc:
            negacoes += 1
            transitorios.append(str(exc))
            if negacoes >= _MAX_NEGACOES:
                problemas.append(
                    f"leitura negada persistentemente pelo SO "
                    f"({negacoes} seguidas): {exc!r}")
                return
        except Exception as exc:
            problemas.append(f"exceção inesperada na leitura: {exc!r}")
            return
        parar.wait(_PAUSA_LEITURA_S)


def _injeta_negacoes(monkeypatch, alvo: Path, negacoes: int) -> None:
    """Faz o `read_text` do `alvo` negar as PRIMEIRAS `negacoes` chamadas."""
    original = Path.read_text
    restantes = [negacoes]

    def falso(self, *args, **kwargs):
        if self == alvo and restantes[0] > 0:
            restantes[0] -= 1
            raise PermissionError(13, "Permission denied (injetado)")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", falso)


class TestEscritaAtomica:
    def test_concurrent_writes_never_raise(self, tmp_path):
        alvo = tmp_path / "desk-state.json"
        erros: list[Exception] = []

        def gravar(i: int):
            try:
                escrever_json_atomico(alvo, {"ciclo": i})
            except Exception as exc:          # a corrida aparecia aqui
                erros.append(exc)

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(gravar, range(200)))
        assert not erros, f"escrita concorrente falhou: {erros[:3]}"

    def test_replace_tolera_bloqueio_transitorio_maior_que_dez_tentativas(
            self, tmp_path, monkeypatch):
        """Antivírus/leitor no Windows pode segurar o destino por > 45 ms."""
        alvo = tmp_path / "estado.json"
        original = os.replace
        chamadas = [0]

        def replace_transitorio(origem, destino):
            chamadas[0] += 1
            if chamadas[0] <= 12:
                raise PermissionError(5, "Acesso negado transitório")
            return original(origem, destino)

        monkeypatch.setattr("tradingagents.desk.storage.os.replace",
                            replace_transitorio)

        escrever_json_atomico(alvo, {"ok": True})

        assert chamadas[0] == 13
        assert json.loads(alvo.read_text(encoding="utf-8")) == {"ok": True}

    def test_file_is_always_complete_and_valid(self, tmp_path):
        """Nunca deve existir um JSON pela metade — nem durante a escrita."""
        alvo = tmp_path / "estado.json"
        escrever_json_atomico(alvo, {"inicial": True})
        problemas: list[str] = []
        transitorios: list[str] = []
        parar = threading.Event()
        t = threading.Thread(target=_vigiar_leitura,
                             args=(alvo, parar, problemas, transitorios),
                             daemon=True)
        t.start()
        try:
            for i in range(150):
                escrever_json_atomico(alvo, {"ciclo": i, "dados": "x" * 500})
        finally:
            parar.set()
            t.join(timeout=5)
        assert not t.is_alive(), "a thread leitora não terminou"
        assert not problemas, (problemas[:3],
                               f"negações transitórias: {len(transitorios)}")

    def test_no_orphan_temp_files_are_left(self, tmp_path):
        alvo = tmp_path / "estado.json"
        for i in range(30):
            escrever_json_atomico(alvo, {"i": i})
        restos = [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"]
        assert not restos, f"temporários órfãos: {restos}"

    def test_temp_is_created_in_the_same_directory(self, tmp_path):
        """Temporário noutro sistema de arquivos tornaria o replace NÃO
        atômico — é por isso que ele nasce ao lado do destino."""
        import inspect
        fonte = inspect.getsource(escrever_json_atomico)
        assert "dir=str(caminho.parent)" in fonte
        assert "os.replace" in fonte and "fsync" in fonte

    def test_content_survives_the_write(self, tmp_path):
        alvo = tmp_path / "estado.json"
        escrever_json_atomico(alvo, {"chave": "acentuação é preservada"})
        assert json.loads(alvo.read_text(encoding="utf-8"))["chave"] == "acentuação é preservada"

    def test_creates_missing_directories(self, tmp_path):
        alvo = tmp_path / "a" / "b" / "c.json"
        escrever_json_atomico(alvo, {"ok": True})
        assert alvo.exists()


class TestProvaDeLeituraNuncaMorreEmSilencio:
    """Meta-testes da própria prova: negação transitória é registrada e
    recuperada; negação persistente vira problema visível — em nenhum caso
    a thread leitora morre em silêncio."""

    def test_negacao_transitoria_e_registrada_e_recuperada(self, tmp_path,
                                                           monkeypatch):
        alvo = tmp_path / "estado.json"
        escrever_json_atomico(alvo, {"ok": True})
        _injeta_negacoes(monkeypatch, alvo, 1)
        problemas: list[str] = []
        transitorios: list[str] = []
        parar = threading.Event()
        t = threading.Thread(target=_vigiar_leitura,
                             args=(alvo, parar, problemas, transitorios),
                             daemon=True)
        t.start()
        limite = time.monotonic() + 2
        while (t.is_alive() and not transitorios and not problemas
               and time.monotonic() < limite):
            time.sleep(0.01)
        parar.set()
        t.join(timeout=5)
        assert not t.is_alive()
        assert len(transitorios) == 1, transitorios
        assert not problemas, problemas

    def test_negacao_persistente_vira_problema_visivel(self, tmp_path,
                                                       monkeypatch):
        alvo = tmp_path / "estado.json"
        escrever_json_atomico(alvo, {"ok": True})
        _injeta_negacoes(monkeypatch, alvo, 10**9)
        problemas: list[str] = []
        transitorios: list[str] = []
        parar = threading.Event()
        t = threading.Thread(target=_vigiar_leitura,
                             args=(alvo, parar, problemas, transitorios),
                             daemon=True)
        t.start()
        t.join(timeout=10)
        assert not t.is_alive(), "deveria desistir após o teto de negações"
        assert any("persistentemente" in p for p in problemas), problemas


class TestLeituraDoArmazemSobDisputa:
    """`carregar` convive com a mesma corrida do Windows que o escritor:
    negação transitória não pode derrubar a leitura, persistente falha alto."""

    def test_negacao_transitoria_ainda_entrega_o_documento(self, tmp_path,
                                                           monkeypatch):
        armazem = ArmazemDeEstado(None, diretorio=tmp_path)
        armazem.salvar("tenant-1", "mesa", {"ciclo": 7})
        _injeta_negacoes(monkeypatch, tmp_path / "tenant-1" / "mesa.json", 1)
        assert armazem.carregar("tenant-1", "mesa") == {"ciclo": 7}

    def test_negacao_persistente_falha_alto(self, tmp_path, monkeypatch):
        armazem = ArmazemDeEstado(None, diretorio=tmp_path)
        armazem.salvar("tenant-1", "mesa", {"ciclo": 7})
        _injeta_negacoes(monkeypatch, tmp_path / "tenant-1" / "mesa.json", 10**9)
        with pytest.raises(PermissionError):
            armazem.carregar("tenant-1", "mesa")


class TestArmazemSobConcorrencia:
    def test_store_handles_parallel_tenants(self, tmp_path):
        armazem = ArmazemDeEstado(None, diretorio=tmp_path)
        erros: list[Exception] = []

        def gravar(n: int):
            try:
                armazem.salvar(f"tenant-{n % 4}", "mesa", {"ciclo": n})
            except Exception as exc:
                erros.append(exc)

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(gravar, range(120)))
        assert not erros
        for n in range(4):
            assert armazem.carregar(f"tenant-{n}", "mesa") is not None


class TestAgendadorGravaEmCadaCiclo:
    def test_scheduler_persists_repeatedly_without_error(self, tmp_path):
        """Reproduz o cenário do CI: muitos ciclos seguidos gravando estado."""
        from tradingagents.desk.scheduler import DeskScheduler, SchedulerState
        caminho = tmp_path / "desk-state.json"

        class Falso(DeskScheduler):
            def __init__(self):
                self.state_path = caminho
                self.armazem = None
                self.tenant_id = "legacy"
                self.state = SchedulerState()
                self.coordinator = type("C", (), {
                    "pending_reconciliation": staticmethod(lambda: [])})()
                self.orchestrator = type("O", (), {
                    "open_exposure": staticmethod(lambda: [])})()

        s = Falso()
        for i in range(100):
            s.state.cycles_run = i
            s.save()
        assert json.loads(caminho.read_text())["cycles_run"] == 99
        assert not [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
