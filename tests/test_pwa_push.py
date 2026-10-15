"""Web Push (VAPID) do PWA — núcleo, honestidade e CRIPTO REAL.

O envio de rede nunca acontece: o teste de cripto injeta uma sessão falsa no
``requests`` do pywebpush — a criptografia (http_ece), a assinatura VAPID e a
montagem dos headers são as DE VERDADE. Sem pywebpush instalado, só este
módulo de teste é pulado (declarado); o produto continua com estado honesto.
"""
from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone

import pytest

from tradingagents.desk.push_web import (
    CHAVE_ASSINATURAS,
    LIMITE_DISPOSITIVOS,
    URL_DO_TOQUE,
    AssinaturaPush,
    EntregadorPush,
    carregar_assinaturas,
    estado_publico,
    gerar_chaves_vapid,
    montar_payload,
    salvar_assinaturas,
    ttl_e_urgencia,
)


class AlertaFalso:
    def __init__(self, severidade="informativo"):
        self.id = "01f15a4b8d67"
        self.tipo = "oportunidade"
        self.severidade = severidade
        self.titulo = "Oportunidade em BTC/USDT"
        self.detalhe = "Compra sugerida com stop definido"
        self.acao = "Abra o painel para detalhes"


def _assinatura_valida(**troca) -> AssinaturaPush:
    dados = {
        "endpoint": "https://fcm.googleapis.com/fcm/send/abc123token",
        "p256dh": "B" + "a" * 60,
        "auth": "c" * 22,
        "rotulo": "Galaxy S23",
        "criado_em": "2026-10-14T10:00:00+00:00",
    }
    dados.update(troca)
    return AssinaturaPush(**dados)


class TestValidacaoDeAssinatura:
    def test_assinatura_valida_passa(self):
        assert _assinatura_valida().validar() == ""

    @pytest.mark.parametrize("troca,motivo", [
        ({"endpoint": "http://inseguro.example/push"}, "https"),
        ({"endpoint": "https://fcm.googleapis.com/" + "x" * 2100}, "longo"),
        ({"endpoint": "https://a.example/x y"}, "inválidos"),
        ({"p256dh": "curto"}, "p256dh"),
        ({"auth": "curto"}, "auth"),
        ({"endpoint": ""}, "https"),
    ])
    def test_invalida_e_recusada_com_motivo(self, troca, motivo):
        assert motivo in _assinatura_valida(**troca).validar()

    def test_identificador_nao_expoe_endpoint(self):
        a = _assinatura_valida()
        assert a.identificador() not in a.endpoint
        assert len(a.identificador()) == 10

    def test_resumo_nunca_contem_segredos(self):
        resumo = _assinatura_valida().resumo()
        assert "endpoint" not in resumo and "p256dh" not in resumo
        assert "auth" not in resumo
        assert resumo["rotulo"] == "Galaxy S23"

    def test_from_dict_aceita_formato_do_navegador(self):
        # PushSubscription.toJSON() tem keys aninhadas — e o servidor aceita.
        a = AssinaturaPush.from_dict({
            "endpoint": "https://fcm.googleapis.com/fcm/send/xyz",
            "keys": {"p256dh": "B" * 60, "auth": "c" * 22},
        })
        assert a.validar() == ""
        assert a.p256dh == "B" * 60 and a.auth == "c" * 22


class TestPersistenciaNoConfig:
    def test_ida_e_volta_com_limite(self):
        cfg = {}
        assinaturas = [_assinatura_valida(
            endpoint=f"https://fcm.googleapis.com/fcm/send/tok{i}",
            p256dh="B" + "a" * 60, auth="c" * 22) for i in range(4)]
        salvar_assinaturas(cfg, assinaturas)
        lidas = carregar_assinaturas(cfg)
        assert len(lidas) == 4
        assert [a.endpoint for a in lidas] == [a.endpoint for a in assinaturas]

    def test_lixo_no_config_vira_lista_vazia(self):
        assert carregar_assinaturas({CHAVE_ASSINATURAS: "não é json"}) == []
        assert carregar_assinaturas({CHAVE_ASSINATURAS: {"a": 1}}) == []
        assert carregar_assinaturas({CHAVE_ASSINATURAS: [1, "x", None]}) == []

    def test_invalida_nao_e_carregada(self):
        cfg = {CHAVE_ASSINATURAS: json.dumps([
            _assinatura_valida().to_dict(),
            _assinatura_valida(endpoint="ftp://nada").to_dict(),
        ])}
        assert len(carregar_assinaturas(cfg)) == 1

    def test_limite_de_dispositivos_e_aplicado_na_leitura(self):
        cfg = {CHAVE_ASSINATURAS: json.dumps([
            _assinatura_valida(
                endpoint=f"https://fcm.googleapis.com/fcm/send/t{i}").to_dict()
            for i in range(LIMITE_DISPOSITIVOS + 5)])}
        assert len(carregar_assinaturas(cfg)) == LIMITE_DISPOSITIVOS


class TestPayloadETtl:
    def test_payload_minimo_sem_dado_sensivel_na_url(self):
        p = montar_payload(AlertaFalso())
        assert p["url"] == URL_DO_TOQUE == "/app#notificacoes"
        assert "#" in p["url"] and "?" not in p["url"]
        assert p["id"] and p["titulo"] and p["corpo"]

    def test_payload_trunca_textos_longos(self):
        alerta = AlertaFalso()
        alerta.titulo = "T" * 500
        alerta.detalhe = "D" * 900
        p = montar_payload(alerta)
        assert len(p["titulo"]) == 120 and len(p["corpo"]) == 280

    def test_ttl_e_urgencia_por_severidade(self):
        assert ttl_e_urgencia("urgente") == (900, "high")
        assert ttl_e_urgencia("emergencia") == (900, "high")
        assert ttl_e_urgencia("atencao") == (3600, "normal")
        assert ttl_e_urgencia("informativo") == (86400, "low")
        assert ttl_e_urgencia("desconhecida") == (3600, "normal")


class TestEntregaHonesta:
    def test_sem_chave_privada_recusa_com_instrucao(self):
        r = EntregadorPush([_assinatura_valida()]).enviar(AlertaFalso())
        assert r["ok"] is False and "VAPID" in r["erro"]

    def test_sem_assinaturas_recusa(self):
        r = EntregadorPush([], vapid_privada="pem").enviar(AlertaFalso())
        assert r["ok"] is False and "inscrito" in r["erro"]

    def test_entrega_marca_ultimo_ok_e_conta(self):
        a1, a2 = _assinatura_valida(), _assinatura_valida(
            endpoint="https://fcm.googleapis.com/fcm/send/outro")
        enviados = []
        e = EntregadorPush([a1, a2], vapid_privada="pem",
                           remetente=lambda a, p, ttl, u: enviados.append(
                               (a.identificador(), p, ttl, u)))
        r = e.enviar(AlertaFalso("urgente"))
        assert r["ok"] and r["entregues"] == 2
        assert len(enviados) == 2 and enviados[0][2] == 900
        assert a1.ultimo_ok and a1.falhas == 0

    def test_erro_generico_conta_falha_e_nao_finge(self):
        def quebra(a, p, ttl, u):
            raise TimeoutError("sem rede")
        a = _assinatura_valida()
        r = EntregadorPush([a], vapid_privada="pem",
                           remetente=quebra).enviar(AlertaFalso())
        assert r["ok"] is False and r["erros"] == 1
        assert a.falhas == 1 and "TimeoutError" in a.ultimo_erro

    def test_apenas_uma_entregue_ainda_e_sucesso_com_detalhe(self):
        a_bom, a_ruim = _assinatura_valida(), _assinatura_valida(
            endpoint="https://fcm.googleapis.com/fcm/send/ruim")

        def seletivo(a, p, ttl, u):
            if "ruim" in a.endpoint:
                raise TimeoutError("sem rede")
        r = EntregadorPush([a_bom, a_ruim], vapid_privada="pem",
                           remetente=seletivo).enviar(AlertaFalso())
        assert r["ok"] and r["entregues"] == 1 and r["erros"] == 1

    def test_entrega_parcial_e_explicita_e_conta_o_aparelho_que_falhou(self):
        # V9-PWA-04: entrega em 1 de 2 NÃO pode parecer entrega total — o
        # resultado carrega "parcial" e o detalhe mostra a lacuna; o aparelho
        # que falhou acumula a falha real.
        a_bom, a_ruim = _assinatura_valida(), _assinatura_valida(
            endpoint="https://fcm.googleapis.com/fcm/send/ruim")

        def seletivo(a, p, ttl, u):
            if "ruim" in a.endpoint:
                raise TimeoutError("sem rede")
        r = EntregadorPush([a_bom, a_ruim], vapid_privada="pem",
                           remetente=seletivo).enviar(AlertaFalso())
        assert r["ok"] and r["entregues"] == 1 and r["erros"] == 1
        assert r.get("parcial") is True
        assert "1/2" in str(r.get("detalhe", ""))
        assert a_ruim.falhas == 1 and "TimeoutError" in a_ruim.ultimo_erro
        assert a_bom.ultimo_ok and a_bom.falhas == 0

    def test_entrega_total_nao_e_marcada_como_parcial(self):
        a1, a2 = _assinatura_valida(), _assinatura_valida(
            endpoint="https://fcm.googleapis.com/fcm/send/outro")

        r = EntregadorPush([a1, a2], vapid_privada="pem",
                           remetente=lambda a, p, ttl, u: None).enviar(
                               AlertaFalso())
        assert r["ok"] and r["entregues"] == 2
        assert r.get("parcial") is False

    def test_expirada_e_podada_com_callback(self):
        class Resposta:
            status_code = 410
            reason = "Gone"
            text = "expired"

        import pywebpush
        a_viva, a_morta = _assinatura_valida(), _assinatura_valida(
            endpoint="https://fcm.googleapis.com/fcm/send/morto")

        def seletivo(assinatura, payload, ttl, urgencia):
            if "morto" in assinatura.endpoint:
                raise pywebpush.WebPushException("expirou", response=Resposta())
        podadas = []
        e = EntregadorPush([a_viva, a_morta], vapid_privada="pem",
                           remetente=seletivo, podar=podadas.extend)
        r = e.enviar(AlertaFalso())
        assert r["ok"] and r["entregues"] == 1
        assert [a.identificador() for a in podadas] == [a_morta.identificador()]
        assert a_morta.identificador() in r["expiradas"]
        # V9-PWA-04: 1 entregue + 1 expirada é uma lacuna — parcial, não total.
        assert r.get("parcial") is True

    def test_todas_expiradas_manda_reinscrever(self):
        import pywebpush

        class Resposta:
            status_code = 404
            reason = "Not Found"
            text = "no"

        def sempre_expirada(a, p, ttl, u):
            raise pywebpush.WebPushException("sumiu", response=Resposta())
        r = EntregadorPush([_assinatura_valida()], vapid_privada="pem",
                           remetente=sempre_expirada,
                           podar=lambda x: None).enviar(AlertaFalso())
        assert r["ok"] is False and "reinscreva" in r["erro"]

    def test_poda_que_falha_nao_derruba_a_entrega(self):
        import pywebpush

        class Resposta:
            status_code = 410

        def um_ok_um_expirado(a, p, ttl, u):
            if "morto" in a.endpoint:
                raise pywebpush.WebPushException("x", response=Resposta())

        def podar_quebrado(x):
            raise OSError("disco cheio")
        r = EntregadorPush(
            [_assinatura_valida(),
             _assinatura_valida(endpoint="https://fcm.googleapis.com/morto")],
            vapid_privada="pem", remetente=um_ok_um_expirado,
            podar=podar_quebrado).enviar(AlertaFalso())
        assert r["ok"] and r["entregues"] == 1


class TestEstadoPublico:
    def test_estado_nunca_contem_chave_privada_nem_endpoint(self):
        cfg = {"ALERTA_WEBPUSH": "true", "ALERTA_VAPID_PUBLICA": "pub123",
               "ALERTA_VAPID_PRIVADA": "segredo-pem"}
        salvar_assinaturas(cfg, [_assinatura_valida()])
        estado = estado_publico(cfg)
        texto = json.dumps(estado, ensure_ascii=False)
        assert "segredo-pem" not in texto
        assert "fcm.googleapis.com" not in texto
        assert estado["tem_privada"] and estado["total"] == 1
        assert estado["vapid_publica"] == "pub123"


class TestChavesVapid:
    def test_geracao_produz_par_utilizavel(self):
        chaves = gerar_chaves_vapid()
        bruto = base64.urlsafe_b64decode(chaves["publica"] + "==")
        # Ponto EC não comprimido P-256: prefixo 0x04 e 65 bytes.
        assert bruto[0] == 0x04 and len(bruto) == 65
        assert "BEGIN PRIVATE KEY" in chaves["privada_pem"]
        from py_vapid import Vapid
        assert Vapid.from_pem(chaves["privada_pem"].encode("utf-8")) is not None


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("pywebpush") is None,
    reason="pywebpush não instalado (extra [push]) — cripto real não executada")
class TestCriptoRealSemRede:
    def _sessao_falsa(self, capturado):
        class SessaoFalsa:
            def post(self, url, data=None, headers=None, timeout=None, **kw):
                capturado.update(url=url, data=data, headers=dict(headers or {}),
                                 timeout=timeout)

                class Resposta:
                    status_code = 201
                    reason = "Created"
                    text = ""
                    headers: dict = {}
                return Resposta()
        return SessaoFalsa()

    def _entregador_com_sessao_falsa(self, capturado):
        import pywebpush
        real = pywebpush.webpush

        def com_sessao(subscription_info, data=None, vapid_private_key=None,
                       vapid_claims=None, **kw):
            return real(subscription_info, data=data,
                        vapid_private_key=vapid_private_key,
                        vapid_claims=vapid_claims,
                        requests_session=self._sessao_falsa(capturado), **kw)
        return com_sessao

    def test_criptografia_e_assinatura_vapid_reais(self, monkeypatch):
        import pywebpush
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        capturado = {}
        monkeypatch.setattr(pywebpush, "webpush",
                            self._entregador_com_sessao_falsa(capturado))

        chaves = gerar_chaves_vapid()
        cliente = ec.generate_private_key(ec.SECP256R1())
        p256dh = base64.urlsafe_b64encode(
            cliente.public_key().public_bytes(
                serialization.Encoding.X962,
                serialization.PublicFormat.UncompressedPoint)
        ).rstrip(b"=").decode()
        auth = base64.urlsafe_b64encode(os.urandom(16)).rstrip(b"=").decode()
        assinatura = AssinaturaPush(
            endpoint="https://fcm.googleapis.com/fcm/send/cripto-real",
            p256dh=p256dh, auth=auth, rotulo="teste",
            criado_em=datetime.now(timezone.utc).isoformat())

        e = EntregadorPush([assinatura], vapid_privada=chaves["privada_pem"],
                           vapid_publica=chaves["publica"])
        r = e.enviar(AlertaFalso("urgente"))

        assert r["ok"] and r["entregues"] == 1
        assert capturado["url"] == assinatura.endpoint
        headers = {k.lower(): v for k, v in capturado["headers"].items()}
        # Estes headers só existem se a assinatura VAPID e a cripto REAIS
        # rodaram (JWT es256 + payload aes128gcm) — teste não é vácuo.
        assert headers["authorization"].startswith("vapid t=")
        assert "k=" in headers["authorization"]
        assert headers["ttl"] == "900"
        assert headers["urgency"] == "high"
        assert headers["content-encoding"] == "aes128gcm"
        assert isinstance(capturado["data"], (bytes, bytearray))
        assert len(capturado["data"]) > 100
        # O rim do payload cifrado não contém o texto claro do alerta.
        assert b"BTC/USDT" not in bytes(capturado["data"])
        assert assinatura.ultimo_ok


_TEM_PY_VAPID = __import__("importlib").util.find_spec("py_vapid") is not None


class TestRotasPush:
    """Rotas /v1/push/* no app real (TestClient): geram, inscrevem, listam e
    cancelam SEM NUNCA devolver a chave privada nem o endpoint."""

    def _app(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        import importlib

        import service.app as m
        importlib.reload(m)
        m._FAILS.clear()
        m._DESKS.clear()
        from fastapi.testclient import TestClient
        return m, TestClient(m.app), {"Authorization": "Bearer s3nh4"}

    def _corpo(self, sufixo=""):
        return {"endpoint": f"https://fcm.googleapis.com/fcm/send/tok{sufixo}",
                "p256dh": "B" + "a" * 60, "auth": "c" * 22,
                "rotulo": "Meu celular"}

    def test_estado_sem_auth_recusa(self, tmp_path, monkeypatch):
        _, c, _ = self._app(tmp_path, monkeypatch)
        assert c.get("/v1/push/estado").status_code == 401

    def test_estado_inicial_honesto_sem_segredo(self, tmp_path, monkeypatch):
        _, c, h = self._app(tmp_path, monkeypatch)
        r = c.get("/v1/push/estado", headers=h)
        assert r.status_code == 200
        d = r.json()
        assert d["habilitado"] is False and d["tem_privada"] is False
        assert d["total"] == 0 and d["limite"] == LIMITE_DISPOSITIVOS
        assert d["url_do_toque"] == URL_DO_TOQUE
        assert "ALERTA_VAPID_PRIVADA" not in r.text

    def test_cabecalhos_de_seguranca(self, tmp_path, monkeypatch):
        _, c, h = self._app(tmp_path, monkeypatch)
        painel = c.get("/app")
        assert "content-security-policy" in painel.headers
        assert painel.headers.get("cache-control") == "no-store"
        api = c.get("/v1/push/estado", headers=h)
        assert api.headers.get("cache-control") == "no-store"
        assert api.headers.get("x-content-type-options") == "nosniff"

    def test_csp_nao_quebra_o_iframe_do_floor(self, tmp_path, monkeypatch):
        # O painel embute /floor num iframe same-origin. frame-ancestors
        # 'none' no HTML do floor faria o navegador recusar o embed
        # (regressão real pega pelo e2e test_floor_embutido_same_origin).
        _, c, _ = self._app(tmp_path, monkeypatch)
        floor = c.get("/floor")
        csp_floor = floor.headers.get("content-security-policy", "")
        assert "frame-ancestors 'self'" in csp_floor
        painel = c.get("/app")
        assert "frame-ancestors 'none'" in painel.headers.get("content-security-policy", "")

    @pytest.mark.skipif(not _TEM_PY_VAPID, reason="py_vapid ausente")
    def test_vapid_gerar_persiste_e_recusa_sobrescrever(self, tmp_path, monkeypatch):
        _, c, h = self._app(tmp_path, monkeypatch)
        r = c.post("/v1/push/vapid/gerar", headers=h, json={})
        assert r.status_code == 200
        publica = r.json()["publica"]
        assert publica and "BEGIN PRIVATE KEY" not in r.text

        cfg = c.get("/v1/config", headers=h).json()["config"]
        assert cfg["ALERTA_VAPID_PUBLICA"]["set"] is True
        assert cfg["ALERTA_VAPID_PRIVADA"]["set"] is True
        assert "value" not in cfg["ALERTA_VAPID_PRIVADA"]

        d = c.get("/v1/push/estado", headers=h).json()
        assert d["tem_privada"] is True and d["vapid_publica"] == publica

        assert c.post("/v1/push/vapid/gerar", headers=h,
                      json={}).status_code == 409
        r2 = c.post("/v1/push/vapid/gerar", headers=h, json={"forcar": True})
        assert r2.status_code == 200 and r2.json()["assinaturas_zeradas"] is True

    @pytest.mark.skipif(not _TEM_PY_VAPID, reason="py_vapid ausente")
    def test_assinar_valida_deduplica_e_nao_ecoa_endpoint(self, tmp_path, monkeypatch):
        _, c, h = self._app(tmp_path, monkeypatch)
        assert c.post("/v1/push/assinar", headers=h,
                      json=self._corpo()).status_code == 409  # sem chaves
        c.post("/v1/push/vapid/gerar", headers=h, json={})

        invalido = c.post("/v1/push/assinar", headers=h,
                          json={**self._corpo(), "endpoint": "http://x"})
        assert invalido.status_code == 400

        r = c.post("/v1/push/assinar", headers=h, json=self._corpo())
        assert r.status_code == 200
        d = r.json()
        assert d["total"] == 1 and len(d["id"]) == 10
        assert self._corpo()["endpoint"] not in r.text

        # Mesmo aparelho reassinando (navegador troca as chaves): dedupe.
        r2 = c.post("/v1/push/assinar", headers=h,
                    json={**self._corpo(), "p256dh": "B" + "b" * 60})
        assert r2.json()["total"] == 1 and r2.json()["id"] == d["id"]

        estado = c.get("/v1/push/estado", headers=h).json()
        assert estado["total"] == 1
        assert self._corpo()["endpoint"] not in json.dumps(estado)

    @pytest.mark.skipif(not _TEM_PY_VAPID, reason="py_vapid ausente")
    def test_assinar_respeita_limite(self, tmp_path, monkeypatch):
        _, c, h = self._app(tmp_path, monkeypatch)
        c.post("/v1/push/vapid/gerar", headers=h, json={})
        for i in range(LIMITE_DISPOSITIVOS):
            assert c.post("/v1/push/assinar", headers=h,
                          json=self._corpo(str(i))).status_code == 200
        excedente = c.post("/v1/push/assinar", headers=h,
                           json=self._corpo("excedente"))
        assert excedente.status_code == 409
        assert "limite" in excedente.json()["detail"]

    @pytest.mark.skipif(not _TEM_PY_VAPID, reason="py_vapid ausente")
    def test_cancelar_remove_aparelho(self, tmp_path, monkeypatch):
        _, c, h = self._app(tmp_path, monkeypatch)
        c.post("/v1/push/vapid/gerar", headers=h, json={})
        corpo = self._corpo()
        alvo = c.post("/v1/push/assinar", headers=h, json=corpo).json()["id"]

        assert c.post("/v1/push/cancelar", headers=h,
                      json={}).status_code == 422
        assert c.post("/v1/push/cancelar", headers=h,
                      json={"id": "naoexiste0"}).status_code == 404

        r = c.post("/v1/push/cancelar", headers=h, json={"id": alvo})
        assert r.status_code == 200 and r.json()["total"] == 0
        assert c.get("/v1/push/estado", headers=h).json()["total"] == 0

    # ----- V9-PWA-03: diagnóstico de envio persistido (não só na RAM) -----

    def _preparar_push(self, c, h):
        """Chaves + WebPush ligado + toast do Windows desligado + 1 aparelho:
        o mínimo para o despacho REAL passar pelo canal webpush."""
        c.post("/v1/push/vapid/gerar", headers=h, json={})
        r = c.post("/v1/config", headers=h, json={"vars": {
            "ALERTA_WEBPUSH": "true", "ALERTA_WINDOWS": "false"}})
        assert r.status_code == 200
        r2 = c.post("/v1/push/assinar", headers=h, json=self._corpo())
        assert r2.status_code == 200

    def _despachar_com_remetente(self, m, c, h, monkeypatch, remetente):
        """Despacho real (POST /v1/alertas/verificar) com o remetente de rede
        trocado — nenhum byte sai do processo."""
        original = m._push_do_tenant

        def push_fake(tid):
            assinaturas, entregador = original(tid)
            entregador._remetente = remetente
            return assinaturas, entregador

        monkeypatch.setattr(m, "_push_do_tenant", push_fake)
        c.get("/v1/desk/status", headers=h)
        m._FILAS_ALERTAS["legacy"].enfileirar(
            tipo="parada", severidade="urgente", titulo="T",
            detalhe="D", acao="A")
        r = c.post("/v1/alertas/verificar", headers=h)
        assert r.status_code == 200

    @pytest.mark.skipif(not _TEM_PY_VAPID, reason="py_vapid ausente")
    def test_estado_guarda_diagnostico_real_apos_envio(self, tmp_path,
                                                       monkeypatch):
        m, c, h = self._app(tmp_path, monkeypatch)
        self._preparar_push(c, h)
        self._despachar_com_remetente(
            m, c, h, monkeypatch, lambda a, p, ttl, u: None)

        antes = c.get("/v1/push/estado", headers=h).json()
        assert antes["dispositivos"][0]["ultimo_ok"], \
            "o sucesso do envio precisa ficar persistido no estado"

        # Reinício do serviço: o diagnóstico vem do armazém, não da memória.
        import importlib

        from fastapi.testclient import TestClient
        importlib.reload(m)
        m._FAILS.clear()
        m._DESKS.clear()
        m._FILAS_ALERTAS.clear()
        c2 = TestClient(m.app)
        depois = c2.get("/v1/push/estado", headers=h).json()
        assert depois["dispositivos"][0]["ultimo_ok"], \
            "o diagnóstico precisa sobreviver ao reinício do serviço"
        assert depois["dispositivos"][0]["falhas"] == 0
        assert depois["dispositivos"][0]["ultimo_erro"] == ""

    @pytest.mark.skipif(not _TEM_PY_VAPID, reason="py_vapid ausente")
    def test_estado_guarda_falha_e_contagem_reais_apos_envio(self, tmp_path,
                                                             monkeypatch):
        m, c, h = self._app(tmp_path, monkeypatch)
        self._preparar_push(c, h)

        def remetente_quebrado(a, p, ttl, u):
            raise TimeoutError("sem resposta do serviço de push")

        self._despachar_com_remetente(
            m, c, h, monkeypatch, remetente_quebrado)

        disp = c.get("/v1/push/estado", headers=h).json()["dispositivos"][0]
        assert disp["falhas"] >= 1 and "TimeoutError" in disp["ultimo_erro"], \
            "a falha real do envio precisa aparecer no estado persistido"

    class _Expirado410(Exception):
        """Simula o 410 do serviço de push (inscrição expirada)."""
        response = type("_R", (), {"status_code": 410})()

    @pytest.mark.skipif(not _TEM_PY_VAPID, reason="py_vapid ausente")
    def test_persistencia_nao_ressuscita_inscricao_expirada_no_despacho(
            self, tmp_path, monkeypatch):
        m, c, h = self._app(tmp_path, monkeypatch)
        self._preparar_push(c, h)                    # aparelho bom ("tok")
        c.post("/v1/push/assinar", headers=h, json=self._corpo("velho"))
        assert c.get("/v1/push/estado", headers=h).json()["total"] == 2

        def remetente(a, p, ttl, u):
            if a.endpoint.endswith("tokvelho"):
                raise self._Expirado410()
            return None

        self._despachar_com_remetente(m, c, h, monkeypatch, remetente)

        d = c.get("/v1/push/estado", headers=h).json()
        assert d["total"] == 1, "a inscrição expirada precisa ser podada"
        vivo = d["dispositivos"][0]
        assert vivo["ultimo_ok"], "o aparelho vivo mantém o sucesso real"
        assert vivo["falhas"] == 0 and vivo["ultimo_erro"] == ""

    @pytest.mark.skipif(not _TEM_PY_VAPID, reason="py_vapid ausente")
    def test_teste_parcial_do_webpush_declara_a_lacuna_e_mostra_a_falha(
            self, tmp_path, monkeypatch):
        # V9-PWA-04: o teste do canal webpush com 1 de 2 aparelhos servidos
        # não pode responder como sucesso total: a rota declara "parcial",
        # o diagnóstico mostra o aparelho que falhou e a caixa de entrada
        # registra a lacuna na auditoria da entrega.
        m, c, h = self._app(tmp_path, monkeypatch)
        self._preparar_push(c, h)                      # aparelho bom ("tok")
        r2 = c.post("/v1/push/assinar", headers=h, json=self._corpo("ruim"))
        assert r2.status_code == 200
        assert c.get("/v1/push/estado", headers=h).json()["total"] == 2

        def remetente(a, p, ttl, u):
            if a.endpoint.endswith("tokruim"):
                raise TimeoutError("sem resposta do serviço de push")
            return None

        original = m._push_do_tenant

        def push_fake(tid):
            assinaturas, entregador = original(tid)
            entregador._remetente = remetente
            return assinaturas, entregador

        monkeypatch.setattr(m, "_push_do_tenant", push_fake)
        r = c.post("/v1/notificacoes/testar", headers=h,
                   json={"canal": "webpush"})
        assert r.status_code == 200
        body = r.json()
        assert body.get("parcial") is True, \
            "1 de 2 aparelhos servidos precisa ser declarado parcial"
        assert "1/2" in str(body.get("detalhe", ""))

        d = c.get("/v1/push/estado", headers=h).json()
        assert any(x["falhas"] >= 1 and "TimeoutError" in x["ultimo_erro"]
                   for x in d["dispositivos"]), \
            "o aparelho que falhou precisa aparecer no diagnóstico"
        assert any(x["ultimo_ok"] for x in d["dispositivos"])

        self._despachar_com_remetente(m, c, h, monkeypatch, remetente)
        caixa = c.get("/v1/notificacoes/inbox", headers=h).json()
        assert any(t.get("canal") == "webpush" and t.get("ok")
                   and "1/2" in str(t.get("detalhe", ""))
                   for t in caixa["auditoria"]), \
            "a lacuna da entrega parcial precisa ficar registrada na auditoria"
