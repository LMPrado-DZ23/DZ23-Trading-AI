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

    def __post_init__(self):
        if self.alvo <= 0:
            raise ValueError("alvo precisa ser positivo")
        if self.stop >= 0:
            raise ValueError("stop precisa ser negativo")
        if abs(self.stop) >= self.alvo:
            # Não é regra de mercado, é aritmética: com stop maior que o
            # alvo, a estratégia precisa de taxa de acerto alta só para
            # empatar — e a conta piora com os custos.
            raise ValueError(
                f"stop ({self.stop:.2%}) não pode ser maior ou igual ao alvo "
                f"({self.alvo:.2%}): exigiria acerto alto só para empatar")

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
        """Taxa de acerto necessária para empatar, com custos incluídos."""
        denominador = self.alvo + abs(self.stop)
        return ((abs(self.stop) + custo_ida_e_volta) / denominador)


# Política padrão por estratégia. Ligada ao alvo que a estratégia declara —
# se o alvo muda, a saída muda junto, no backtest E na operação.
def policy_for(movimento_alvo: Decimal, *, max_velas: int = 48) -> ExitPolicy:
    return ExitPolicy(alvo=movimento_alvo, stop=-movimento_alvo / 2,
                      max_velas_na_posicao=max_velas)


__all__ = ["ExitPolicy", "policy_for"]
