"""Camada quantitativa e de IA — SEPARADA dos demais componentes (item 1).

Fronteiras formais do sistema:
- dados de mercado ......... tradingagents.desk.market / tradingagents.dataflows
- análise quantitativa ..... tradingagents.quant (esta camada)
- IA contextual ........... tradingagents.desk.context (só veta ou reduz)
- supervisor de traders ... tradingagents.desk.traders.MarketRegimeSupervisor
- motor de risco .......... tradingagents.desk.risk
- execução ................ tradingagents.desk.execution / tradingagents.execution

Regra do item 9 (governança): nenhum modelo de IA envia ordens, altera limites
de risco ou promove estratégias para LIVE. Esta camada produz apenas leitura
consultiva — cálculo financeiro determinístico aqui é Python puro, nunca LLM.
"""
from __future__ import annotations

from .advisor import ConselheiroQuantitativo, ParecerQuant
from .calibration import CalibradorDeConfianca
from .champion import DecisaoDePromocao, Desempenho, comparar
from .drift import MonitorDeDrift, psi
from .ensemble import Consenso, Voz, consenso
from .features import NOMES_DE_FEATURES, para_vetor, retornos, vetor_de_features
from .governance import ViolacaoDeGovernanca, auditar_aconselhador, exigir_aconselhador
from .models import ClassificadorDeRegime, ModeloIndisponivel, disponibilidade

__all__ = [
    "CalibradorDeConfianca",
    "ClassificadorDeRegime",
    "ConselheiroQuantitativo",
    "Consenso",
    "DecisaoDePromocao",
    "Desempenho",
    "ModeloIndisponivel",
    "MonitorDeDrift",
    "NOMES_DE_FEATURES",
    "ParecerQuant",
    "ViolacaoDeGovernanca",
    "Voz",
    "auditar_aconselhador",
    "comparar",
    "consenso",
    "disponibilidade",
    "exigir_aconselhador",
    "para_vetor",
    "psi",
    "retornos",
    "vetor_de_features",
]
