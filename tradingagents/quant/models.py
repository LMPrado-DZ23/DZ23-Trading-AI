"""Adaptadores experimentais de ML (itens 2 e 4).

XGBoost/LightGBM entram como EXPERIMENTO — disponibilidade detectada em
runtime, nunca presumida superior às estratégias matemáticas da mesa. A
comparação é feita pela infraestrutura champion/challenger (quant.champion);
sem ganho medido, o modelo não sai do laboratório.

LSTM/Transformers (torch) aparecem apenas no registro de disponibilidade:
implementá-los só se justifica APÓS a comparação de desempenho e custo —
prometer antes de medir é fé, não engenharia.

Nenhum adaptador envia ordem, altera limite ou promove para LIVE.
"""
from __future__ import annotations

import importlib
import importlib.util
from importlib.metadata import PackageNotFoundError, version

_BIBLIOTECAS_OPCIONAIS = {
    "xgboost": "classificação de regime (experimental)",
    "lightgbm": "classificação de regime (experimental)",
    "torch": "LSTM/Transformers (experimental, somente após comparação de custo)",
}


class ModeloIndisponivel(RuntimeError):
    """Biblioteca ausente ou modelo não treinado — nunca silenciar isto."""


def disponibilidade() -> dict[str, dict]:
    """O que está instalado, em que versão, e com que papel declarado."""
    resultado: dict[str, dict] = {}
    for nome, papel in _BIBLIOTECAS_OPCIONAIS.items():
        instalado = importlib.util.find_spec(nome) is not None
        versao = None
        if instalado:
            try:
                versao = version(nome)
            except PackageNotFoundError:
                versao = "desconhecida"
        resultado[nome] = {"instalado": instalado, "versao": versao, "papel": papel}
    return resultado


class ClassificadorDeRegime:
    """Wrapper sklearn-like sobre xgboost/lightgbm, alvo = rótulo de regime.

    Contrato seguro: sem biblioteca ou sem treino, `prever_*` LEVANTA
    `ModeloIndisponivel` — quem chama decide o fallback. Previsão silenciosa
    de um modelo que não existe seria a pior das mentiras.
    """

    def __init__(self, biblioteca: str = "lightgbm", **parametros):
        self.biblioteca = biblioteca
        self.parametros = parametros
        self._modelo = None
        self.classes_: list[str] = []

    @property
    def disponivel(self) -> bool:
        return importlib.util.find_spec(self.biblioteca) is not None

    @property
    def treinado(self) -> bool:
        return self._modelo is not None

    def _novo(self):
        if not self.disponivel:
            raise ModeloIndisponivel(
                f"biblioteca '{self.biblioteca}' não instalada — instale o extra "
                "experimental para usar este modelo")
        if self.biblioteca == "xgboost":
            xgb = importlib.import_module("xgboost")
            defaults = {"n_estimators": 80, "max_depth": 3, "learning_rate": 0.1,
                        "subsample": 0.9, "random_state": 42, "verbosity": 0}
            defaults.update(self.parametros)
            return xgb.XGBClassifier(**defaults)
        if self.biblioteca == "lightgbm":
            lgb = importlib.import_module("lightgbm")
            defaults = {"n_estimators": 80, "num_leaves": 15, "random_state": 42,
                        "verbose": -1}
            defaults.update(self.parametros)
            return lgb.LGBMClassifier(**defaults)
        raise ModeloIndisponivel(f"biblioteca '{self.biblioteca}' sem adaptador")

    def treinar(self, X: list[list[float]], y_rotulos: list[str]) -> ClassificadorDeRegime:
        if not X or len(X) != len(y_rotulos):
            raise ValueError("X e y precisam ter o mesmo tamanho não nulo")
        modelo = self._novo()
        self.classes_ = sorted(set(y_rotulos))
        y = [self.classes_.index(rotulo) for rotulo in y_rotulos]
        modelo.fit(X, y)
        self._modelo = modelo
        return self

    def prever_probabilidades(self, X: list[list[float]]) -> list[dict[str, float]]:
        if self._modelo is None:
            raise ModeloIndisponivel("modelo não treinado")
        bruto = self._modelo.predict_proba(X)
        return [{classe: float(p) for classe, p in zip(self.classes_, linha, strict=True)}
                for linha in bruto]
