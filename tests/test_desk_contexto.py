"""IA de contexto: pode vetar e reduzir — nunca aumentar nem autorizar."""
from __future__ import annotations

from decimal import Decimal

from tradingagents.desk.context import (
    AUSENTE,
    AnalistaDeContexto,
    ParecerDeContexto,
    aplicar_parecer,
)


def cliente_que_responde(texto):
    return lambda prompt: texto


def avaliar(analista, exposicao=Decimal("0.15")):
    return analista.avaliar(instrumento="BTC/USDT", acao="buy",
                            exposicao=exposicao, leitura="tendência de alta",
                            quando="2026-09-14T12:00:00Z")


class TestPoderesLimitados:
    def test_veto_zeroes_the_exposure(self):
        p = ParecerDeContexto("vetar", "decisão do Fed em 30 minutos", 0.9)
        nova, _ = aplicar_parecer(Decimal("0.15"), p)
        assert nova == Decimal("0")

    def test_reduce_halves_it(self):
        p = ParecerDeContexto("reduzir", "volatilidade de evento", 0.7)
        nova, _ = aplicar_parecer(Decimal("0.20"), p)
        assert nova == Decimal("0.10")

    def test_ai_can_never_increase_exposure(self):
        """Mesmo que o modelo 'queira' mais, o teto é o que a mesa calculou."""
        class Otimista(ParecerDeContexto):
            @property
            def fator_de_exposicao(self):
                return Decimal("5")
        nova, _ = aplicar_parecer(Decimal("0.10"), Otimista("seguir", "adoro", 1.0))
        assert nova == Decimal("0.10")

    def test_follow_keeps_the_size(self):
        nova, _ = aplicar_parecer(Decimal("0.12"), ParecerDeContexto("seguir", "nada", 0.5))
        assert nova == Decimal("0.12")


class TestRespostaDoModelo:
    def test_valid_json_is_accepted(self):
        a = AnalistaDeContexto(cliente_que_responde(
            '{"veredito":"vetar","motivo":"halving em 2h","confianca":0.9}'))
        p = avaliar(a)
        assert p.veredito == "vetar" and "halving" in p.motivo

    def test_json_inside_markdown_is_accepted(self):
        a = AnalistaDeContexto(cliente_que_responde(
            '```json\n{"veredito":"reduzir","motivo":"CPI hoje","confianca":0.8}\n```'))
        assert avaliar(a).veredito == "reduzir"

    def test_free_text_is_treated_as_absent(self):
        """Modelo que ignora o formato não ganha voto."""
        a = AnalistaDeContexto(cliente_que_responde("Acho melhor não operar agora!"))
        p = avaliar(a)
        assert p.veredito == "seguir" and p.disponivel is False
        assert "ilegível" in p.motivo

    def test_invented_verdict_is_rejected(self):
        a = AnalistaDeContexto(cliente_que_responde(
            '{"veredito":"comprar tudo","motivo":"vai subir","confianca":1}'))
        assert avaliar(a).disponivel is False

    def test_weak_veto_becomes_a_reduction(self):
        """Palpite não para a mesa, mas também não é ignorado."""
        a = AnalistaDeContexto(cliente_que_responde(
            '{"veredito":"vetar","motivo":"acho que vem notícia","confianca":0.2}'))
        p = avaliar(a)
        assert p.veredito == "reduzir" and "confiança baixa" in p.motivo


class TestFalhaNaoParaAMesa:
    def test_no_client_means_no_check_but_no_block(self):
        p = avaliar(AnalistaDeContexto(None))
        assert p.veredito == "seguir" and p.disponivel is False
        assert p.fator_de_exposicao == Decimal("1")

    def test_provider_error_does_not_stop_trading(self):
        def explode(prompt):
            raise RuntimeError("provedor fora do ar")
        p = avaliar(AnalistaDeContexto(explode))
        assert p.veredito == "seguir" and p.disponivel is False
        assert "falha na consulta" in p.motivo

    def test_absence_is_visible_not_silent(self):
        assert "sem verificação de contexto" in AUSENTE.resumo()


class TestNoCicloDaMesa:
    def _mesa(self, analista):
        from tradingagents.desk.models import AgentStage, DeskMandate, Mode
        from tradingagents.desk.orchestrator import DeskOrchestrator
        from tradingagents.desk.risk import RiskEngine
        from tradingagents.desk.traders import default_desk
        m = DeskMandate(mandate_id="m", owner="p", account="a", mode=Mode.PAPER,
                        instruments=("BTC/USDT",), capital=Decimal("1000"))
        traders = default_desk(("BTC/USDT",))
        for t in traders:
            for st in (AgentStage.VALIDATED, AgentStage.SHADOW, AgentStage.PAPER_ENABLED):
                t.spec.move_to(st)
        return DeskOrchestrator(m, traders, worker_id="w", risk=RiskEngine(m),
                                analista=analista)

    def _snap(self):
        from tradingagents.desk.models import MarketSnapshot, utcnow
        serie = tuple(Decimal(str(round(100 * (1.01 ** i), 6))) for i in range(60))
        return MarketSnapshot("BTC/USDT", "1h", utcnow(), serie)

    def test_ai_veto_blocks_the_entry(self):
        a = AnalistaDeContexto(cliente_que_responde(
            '{"veredito":"vetar","motivo":"exchange sob incidente","confianca":0.95}'))
        rep = self._mesa(a).run_cycle([self._snap()], {"BTC/USDT": Decimal("150")})
        assert not rep.intents
        assert any("IA vetou" in motivo for _, motivo in rep.vetoed)

    def test_desk_keeps_working_without_the_ai(self):
        rep = self._mesa(None).run_cycle([self._snap()], {"BTC/USDT": Decimal("150")})
        assert rep.intents, "mesa deve operar mesmo sem analista de contexto"
