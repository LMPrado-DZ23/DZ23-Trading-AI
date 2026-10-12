"""Políticas de saída — uma definição só, consumida por todos os ambientes.

Achado da auditoria: a produção saía em -5%/+8% e o backtest simulava
-alvo/2 e +alvo (ex.: -1,25%/+2,5% na reversão à média). Ou seja, o teste
media uma estratégia DIFERENTE da que opera. Uma estratégia podia aparecer
"aprovada" por causa de saídas que o robô real nunca usaria.

Aqui a política é declarada UMA vez, por estratégia, e consumida por
backtest, paper, shadow, testnet e (quando autorizado) live. Divergência
entre ambientes deixa de ser possível por construção.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ExitPolicy:
    """Quando encerrar. `alvo` e `stop` são frações do preço de entrada."""
    alvo: Decimal
    stop: Decimal                       # valor NEGATIVO
    max_velas_na_posicao: int = 48
    exigir_payoff_minimo: bool = True

    def __post_init__(self):
        if self.alvo <= 0:
            raise ValueError("alvo precisa ser positivo")
        if self.stop >= 0:
            raise ValueError("stop precisa ser negativo")
        if self.exigir_payoff_minimo and abs(self.stop) >= self.alvo:
            # DECISÃO DE PRODUTO da DZ23, não impossibilidade matemática:
            # payoff 1:2 PODE ser lucrativo com acerto suficiente (~67% sem
            # custos). O que a casa exige é payoff >= 1 para não depender de
            # taxa de acerto alta — que é frágil e cara de sustentar.
            # Quem quiser assumir esse perfil, declara exigir_payoff_minimo
            # = False e assume o acerto necessário, que fica no relatório.
            raise ValueError(
                f"política da casa exige payoff >= 1 (alvo {self.alvo:.2%} vs "
                f"stop {self.stop:.2%}); acerto necessário seria "
                f"{self.acerto_minimo():.0%}. Para assumir esse perfil, use "
                "exigir_payoff_minimo=False conscientemente")

    def avaliar(self, retorno: Decimal, velas_na_posicao: int = 0
                ) -> tuple[bool, str]:
        """Decide a saída. A MESMA função para backtest e operação."""
        if retorno >= self.alvo:
            return True, "alvo"
        if retorno <= self.stop:
            return True, "stop"
        if self.max_velas_na_posicao and velas_na_posicao >= self.max_velas_na_posicao:
            return True, "tempo"
        return False, "mantendo"

    def razao_risco_retorno(self) -> Decimal:
        return (self.alvo / abs(self.stop)).quantize(Decimal("0.01"))

    def acerto_minimo(self, custo_ida_e_volta: Decimal = Decimal("0")) -> Decimal:
        """Taxa de acerto necessária para empatar, com custos incluídos.

        É este número — e não uma proibição — que diz se a configuração é
        sustentável. Payoff 1:2 exige ~67%; payoff 2:1 exige ~33%.
        """
        denominador = self.alvo + abs(self.stop)
        return ((abs(self.stop) + custo_ida_e_volta) / denominador)

    def expectativa(self, taxa_acerto: Decimal,
                    custo_ida_e_volta: Decimal = Decimal("0")) -> Decimal:
        """Resultado esperado por operação, já com custos."""
        return (taxa_acerto * self.alvo
                - (Decimal("1") - taxa_acerto) * abs(self.stop)
                - custo_ida_e_volta)


# Política padrão por estratégia. Ligada ao alvo que a estratégia declara —
# se o alvo muda, a saída muda junto, no backtest E na operação.
def policy_for(movimento_alvo: Decimal, *, max_velas: int = 48) -> ExitPolicy:
    return ExitPolicy(alvo=movimento_alvo, stop=-movimento_alvo / 2,
                      max_velas_na_posicao=max_velas)


__all__ = ["ExitPolicy", "policy_for"]
