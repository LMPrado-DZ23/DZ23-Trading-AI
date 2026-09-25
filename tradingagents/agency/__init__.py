"""Núcleo geral da agência multiagente DZ23.

O módulo é agnóstico de LLM e de fornecedor: agentes recebem tarefas
validadas e devolvem resultados verificáveis. O trading permanece em seu
módulo governado separado.
"""

from .contracts import AgencyTask, AgentSpec, EvidenceRef, TaskResult, TaskStatus
from .executor import AgencyExecutor
from .llm_handler import LLMTaskHandler, LLMTaskPolicy, build_llm_handler

__all__ = [
    "AgentSpec",
    "AgencyExecutor",
    "AgencyTask",
    "EvidenceRef",
    "LLMTaskHandler",
    "LLMTaskPolicy",
    "TaskResult",
    "TaskStatus",
    "build_llm_handler",
]
