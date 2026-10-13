"""Conselheiro quantitativo — ponte consultiva com a mesa (item 6).

Fluxo: closes → features determinísticas → (modelo experimental, se existir
e estiver treinado) → calibração → parecer com direção e confiança.

Limites por construção:
- sem janela suficiente, sem modelo ou com falha do modelo: devolve
  `disponivel=False` — a mesa NÃO para, apenas segue sem essa leitura;
- drift detectado reduz a confiança à metade (modelo desatualizado mente);
- nenhuma saída tem tamanho de posição, ordem ou limite: só direção nominal.
"""
from __future__ import annotations

from dataclasses import dataclass

from .features import para_vetor, retornos, vetor_de_features
from .governance import exigir_aconselhador

DIRECAO_POR_CLASSE = {
    "tendencia_alta": "comprar",
    "tendencia_baixa": "vender",
    "lateral": "abster",
    "volatil": "abster",
    "indefinido": "abster",
}


@dataclass(frozen=True)
class ParecerQuant:
    disponivel: bool
    direcao: str              # comprar | vender | abster
    confianca: float
    motivo: str
    notas: tuple[str, ...] = ()


class ConselheiroQuantitativo:
    """Só aconselha: não envia ordem, não altera risco, não promove nada."""

    def __init__(self, modelo=None, *, monitor_de_drift=None, calibrador=None):
        self.modelo = modelo
        self.monitor_de_drift = monitor_de_drift
        self.calibrador = calibrador
        exigir_aconselhador(self)

    @property
    def disponivel(self) -> bool:
        return bool(self.modelo is not None
                    and getattr(self.modelo, "disponivel", False)
                    and getattr(self.modelo, "treinado", False))

    def avaliar(self, instrumento: str, closes) -> ParecerQuant:
        features = vetor_de_features(closes)
        if features is None:
            return ParecerQuant(False, "abster", 0.0,
                                "janela insuficiente para leitura quantitativa")
        if not self.disponivel:
            return ParecerQuant(False, "abster", 0.0,
                                "modelos experimentais indisponíveis ou não treinados")

        notas: list[str] = []
        fator_de_confianca = 1.0
        if self.monitor_de_drift is not None:
            # Drift é medido na distribuição de retornos (a referência do
            # treino): um único vetor de features não forma distribuição.
            drift = self.monitor_de_drift.avaliar(retornos(closes))
            if drift["deriva"]:
                fator_de_confianca = 0.5
                notas.append(f"drift detectado ({drift['motivo']}); "
                             "confiança reduzida até reavaliação")

        try:
            probabilidades = self.modelo.prever_probabilidades(
                [para_vetor(features)])[0]
        except Exception as exc:
            return ParecerQuant(False, "abster", 0.0,
                                f"falha do modelo: {type(exc).__name__}",
                                tuple(notas))

        classe, probabilidade = max(probabilidades.items(), key=lambda kv: kv[1])
        if self.calibrador is not None:
            probabilidade = self.calibrador.calibrar(probabilidade)
        direcao = DIRECAO_POR_CLASSE.get(classe)
        if direcao is None:
            return ParecerQuant(False, "abster", 0.0,
                                f"classe desconhecida '{classe}'", tuple(notas))
        return ParecerQuant(
            True, direcao, float(probabilidade) * fator_de_confianca,
            f"regime previsto: {classe}", tuple(notas))
