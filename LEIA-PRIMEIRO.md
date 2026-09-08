# TradingAgents — Estado: GATE A ENCERRADO (aprovado em auditoria independente)

Este ZIP é o projeto COMPLETO e pronto: fork do TauricResearch/TradingAgents
com a Fase 0 de segurança concluída (12 rodadas de auditoria adversarial
independente, veredito final APROVADO, scan Codex 440ca000).

## Verificar que está íntegro (obrigatório antes de mexer)
    pip install -e .   (ou: pip install -r requirements.txt)
    python -m pytest tests/ -q
    → deve dar EXATAMENTE: 849 passed, 2 skipped, 71 subtests passed
    (2 skips esperados: langchain_aws opcional ausente; teste live DeepSeek
    sem credencial)

## O que já está pronto (NÃO refazer, NÃO "melhorar")
- Sanitização/envelope de toda evidência externa (dataflows/evidence.py,
  sanitize.py) — todo dado externo entra por UntrustedEvidence.
- Redação de segredos fail-closed (dataflows/redact.py) — sobreviveu a
  fuzz de 1.000 vetores adversariais com 0 vazamentos.
- Política de backends/transporte (llm_clients/backend_policy.py,
  transport.py) — allowlist HTTPS, sem redirects, sem proxy ambiental,
  credencial oficial nunca vai a endpoint custom.
- Gate de capital (gates.py) — assert_secure_for_capital fail-closed.
- Todos os consumidores de relatórios/históricos passam pelo assembler
  central (agents/utils/agent_utils.py: gather_wrapped_reports).
Os testes tests/test_security_fase0*.py são o CONTRATO desses controles:
qualquer mudança que os quebre é regressão de segurança.

## Próximo trabalho
Ver PROXIMO-PASSO-GATE-B.md (Marco 1: point-in-time nas fontes —
available_at obrigatório, fundamentals por data de publicação, Polymarket
fail-closed). HANDOFF.md tem o histórico completo de decisões e vereditos.

## Regras que não mudam
- Sizing NUNCA no LLM (direção/convicção = LLM; tamanho = aritmética).
- Nada de broker/ordens/capital real sem gate humano e auditoria própria.
- Segredos só via variáveis de ambiente; nunca em código ou log.
