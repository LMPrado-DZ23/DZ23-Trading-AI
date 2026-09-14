"""Estado durável fora do servidor (servidor provisório não pode levar tudo).

O aprendizado dos agentes e o estado da mesa são o patrimônio do sistema:
levam semanas para se formar e não se recuperam de backup perdido. Enquanto
viviam em arquivo no volume, trocar de servidor apagava tudo.

Aqui eles passam a viver no banco externo (Supabase/Postgres) quando houver
`DATABASE_URL`, e continuam em arquivo quando for instalação local.

Decisões:
- Mesma interface para os dois destinos: o resto do código não muda.
- Escrita atômica no arquivo (tmp + replace) e UPSERT no banco: um
  desligamento no meio não corrompe o que já existia.
- Falha de banco NÃO derruba a mesa: cai para arquivo com aviso. Perder o
  histórico é ruim; parar de operar por causa dele é pior.
"""
from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path


def escrever_json_atomico(caminho: Path, documento: dict) -> None:
    """Grava JSON sem janela de corrupção e sem corrida entre escritas.

    Um NOME FIXO de temporário (state.tmp) parece inofensivo até duas
    gravações concorrerem: a primeira consome o arquivo no replace e a
    segunda não o encontra mais (FileNotFoundError). O temporário aqui é
    único por escrita, criado no MESMO diretório do destino — requisito para
    que os.replace seja atômico — e o conteúdo vai ao disco com fsync antes
    da troca, para que uma queda de energia não deixe arquivo pela metade.
    """
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(caminho.parent),
                               prefix=f".{caminho.stem}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(documento, fh, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, caminho)
    except BaseException:
        # temporário órfão não pode ficar entulhando o volume
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


class ArmazemDeEstado:
    """Chave→documento JSON, por inquilino."""

    def __init__(self, database_url: str | None = None,
                 diretorio: str | Path | None = None):
        self.dsn = (database_url or "").strip() or None
        self.diretorio = Path(diretorio) if diretorio else None
        self.backend = "arquivo"
        self.aviso = ""
        if self.dsn:
            try:
                self._criar_tabela()
                self.backend = "postgres"
            except Exception as exc:
                self.aviso = (f"banco indisponível para o estado da mesa "
                              f"({type(exc).__name__}); usando arquivo local — "
                              "troque de servidor e este histórico se perde")
        if self.backend == "arquivo" and self.diretorio:
            self.diretorio.mkdir(parents=True, exist_ok=True)

    # -- postgres ------------------------------------------------------
    def _conectar(self):
        import psycopg
        return psycopg.connect(self.dsn)

    def _criar_tabela(self) -> None:
        with self._conectar() as c, c.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS dz23_desk_state ("
                "tenant_id TEXT NOT NULL, chave TEXT NOT NULL, "
                "documento JSONB NOT NULL, "
                "atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now(), "
                "PRIMARY KEY (tenant_id, chave))")

    # -- interface -----------------------------------------------------
    def _caminho(self, tenant_id: str, chave: str) -> Path:
        base = self.diretorio or Path(".")
        return base / tenant_id / f"{chave}.json"

    def carregar(self, tenant_id: str, chave: str) -> dict | None:
        if self.backend == "postgres":
            try:
                with self._conectar() as c, c.cursor() as cur:
                    cur.execute("SELECT documento FROM dz23_desk_state "
                                "WHERE tenant_id=%s AND chave=%s",
                                (tenant_id, chave))
                    linha = cur.fetchone()
                return linha[0] if linha else None
            except Exception:
                pass                      # cai para arquivo silenciosamente
        caminho = self._caminho(tenant_id, chave)
        if not caminho.exists():
            return None
        try:
            return json.loads(caminho.read_text())
        except ValueError:
            return None

    def salvar(self, tenant_id: str, chave: str, documento: dict) -> str:
        """Devolve onde gravou — o painel mostra isso ao dono."""
        if self.backend == "postgres":
            try:
                with self._conectar() as c, c.cursor() as cur:
                    cur.execute(
                        "INSERT INTO dz23_desk_state (tenant_id, chave, documento) "
                        "VALUES (%s, %s, %s) "
                        "ON CONFLICT (tenant_id, chave) DO UPDATE SET "
                        "documento = EXCLUDED.documento, atualizado_em = now()",
                        (tenant_id, chave, json.dumps(documento, ensure_ascii=False)))
                return "postgres"
            except Exception as exc:
                self.aviso = (f"falha ao gravar no banco ({type(exc).__name__}); "
                              "gravado em arquivo local")
        escrever_json_atomico(self._caminho(tenant_id, chave), documento)
        return "arquivo"

    def listar(self, tenant_id: str) -> list[str]:
        if self.backend == "postgres":
            try:
                with self._conectar() as c, c.cursor() as cur:
                    cur.execute("SELECT chave FROM dz23_desk_state WHERE tenant_id=%s",
                                (tenant_id,))
                    return sorted(linha[0] for linha in cur.fetchall())
            except Exception:
                pass
        base = (self.diretorio or Path(".")) / tenant_id
        if not base.exists():
            return []
        return sorted(p.stem for p in base.glob("*.json"))

    def estado(self) -> dict:
        return {"destino": self.backend,
                "durabilidade": ("sobrevive à troca de servidor"
                                 if self.backend == "postgres" else
                                 "vive no volume deste servidor — se ele for "
                                 "descartado, o histórico se perde"),
                "aviso": self.aviso}


__all__ = ["ArmazemDeEstado", "escrever_json_atomico"]
