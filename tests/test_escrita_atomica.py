"""Escrita atômica sob concorrência.

Bug real que o CI pegou (e meus testes não): a gravação usava um temporário
de NOME FIXO. Com o laço gravando a cada ciclo, duas escritas disputavam o
mesmo .tmp — a primeira o consumia no replace e a segunda estourava
FileNotFoundError. Passava no Python 3.13 e falhava no 3.10/3.11/3.12: puro
acaso de temporização.

Estes testes não dependem de sorte: forçam a concorrência.
"""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor

from tradingagents.desk.storage import ArmazemDeEstado, escrever_json_atomico


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

    def test_file_is_always_complete_and_valid(self, tmp_path):
        """Nunca deve existir um JSON pela metade — nem durante a escrita."""
        alvo = tmp_path / "estado.json"
        escrever_json_atomico(alvo, {"inicial": True})
        problemas: list[str] = []
        parar = threading.Event()

        def leitor():
            while not parar.is_set():
                try:
                    json.loads(alvo.read_text())
                except ValueError:
                    problemas.append("json incompleto observado")
                except FileNotFoundError:
                    problemas.append("arquivo sumiu durante a troca")

        t = threading.Thread(target=leitor, daemon=True)
        t.start()
        try:
            for i in range(150):
                escrever_json_atomico(alvo, {"ciclo": i, "dados": "x" * 500})
        finally:
            parar.set()
            t.join(timeout=5)
        assert not problemas, problemas[:3]

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
        assert json.loads(alvo.read_text())["chave"] == "acentuação é preservada"

    def test_creates_missing_directories(self, tmp_path):
        alvo = tmp_path / "a" / "b" / "c.json"
        escrever_json_atomico(alvo, {"ok": True})
        assert alvo.exists()


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
