# FIN-2 — Gates de autorização financeira comprovados por matriz adversarial (auditoria ChatGPT, commit 4b83e16)

**Achado (auditoria independente):** `tradingagents/gates.py::assert_secure_for_capital()`
verifica a segurança do TRANSPORTE (TLS/host/modo) — não a AUTORIZAÇÃO
financeira. O auditor pediu comprovação SEPARADA dos seis gates:
proprietário, conta, estratégia, kill-switch, limites e reconciliação.

**Resposta (commit `3ee3fe0` — só testes):** `tests/test_auditoria_fin2_gates_financeiros.py`
(546 linhas, 45 testes, 8 classes) prova cada gate nos DOIS sentidos: a
tentativa adversária é RECUSADA e o caminho legítimo ABRE — um gate que
nunca abre é indistinguível de um gate quebrado. Nenhum código de produção
foi alterado: a matriz foi escrita contra o código vigente e nenhum dos
seis gates exigiu correção sob esta leitura. Zero rede, zero ordens reais —
apenas corretora simulada e adaptador Paper.

## Matriz por gate (classe → sentidos provados)

| Gate | Provas adversariais e de abertura |
|---|---|
| **Proprietário** | `TestGateDoProprietario`: rotas de dinheiro (`/v1/config`, `/v1/desk/status`, `/v1/live/status`, `POST /v1/live/parada-emergencia`) respondem 401 sem token; token errado também 401; o dono entra |
| **Conta** | `TestGateDaConta`: router recusa conta trocada na intenção; coordenador recusa intenção de outra conta; reserva com conta trocada NÃO libera o dinheiro; relatório de outra conta não autoriza; intenção da conta certa passa |
| **Estratégia** | `TestGateDaEstrategia`: router recusa estratégia trocada e quantidade trocada; classe sem execução homologada não abre sala; classe homologada abre; esta camada não cria sala LIVE |
| **Kill-switch** | `TestGateDoKillSwitch` (8): acionado recusa execução; desarme só com reconciliação confirmada; arquivo de estado ilegível ⇒ engajado por precaução; parada de emergência da mesa persiste; liberar exige frase do dono E reconciliação; liberar com as duas condições funciona; armazém ilegível bloqueia; estado desconhecido no disco vira acionado |
| **Limites** | `TestGateDeLimites`: teto por posição limita a exposição; capital insuficiente veta sem enviar; limite de posições simultâneas veta; entrada sem reserva é recusada |
| **Reconciliação** | `TestGateDeReconciliacao`: router sem guard é fail-closed; portão da mesa bloqueia compra E venda; reconciliação confirmada reabre o portão; reconciliação sem acesso à corretora não é declarada segura |
| **Mandato (fronteira de autorização)** | `TestGateDoMandato` (9): revogado, expirado, instrumento fora do mandato, worker sem posse da intenção, intenção expirada, política mudada desde a reserva — todos recusam; LIVE sem autorização recusa na execução E na política |
| **Saídas do gate não são becos** | `TestSaidasDoGateNaoSaoBecos`: entrada legítima atravessa TODOS os gates; replay idempotente não duplica; agente sem estágio não propõe |

## Provas

| Evidência | Comando / resultado |
|---|---|
| GREEN | `tests/test_auditoria_fin2_gates_financeiros.py` — **45 passed** (fresco, 2026-10-14) |
| Trio da auditoria | SEC-1 (15) + FIN-1 (7) + FIN-2 (45) = **67 passed** na mesma execução |
| Natureza do commit | `git show --stat 3ee3fe0` — 1 arquivo, 546 inserções, só testes |
| Rede / ordens | Nenhuma — corretora simulada + adaptador Paper; nenhum envio real |

```
.venv/Scripts/python.exe -m pytest tests/test_auditoria_sec1_segredos_em_repouso.py \
  tests/test_auditoria_fin1_live_desk_seal.py \
  tests/test_auditoria_fin2_gates_financeiros.py -q
# 67 passed
```

## Defesa contínua (fora desta matriz)

`test_execution_router`, `test_crash_restart`, `test_service_leigo` e
`test_modo_da_mesa` continuam cobrindo os mesmos gates no fluxo integrado —
esta suíte é a leitura POR ACHADO, como o auditor pediu.

## Limitações declaradas (honestidade)

1. **Não é um inventário rota-a-rota de autorização**: a matriz prova os
   gates nos pontos de decisão (roteador de execução + coordenador da
   mesa), não cada endpoint do serviço um por um. Inventário rota-a-rota
   permanece declarado como não feito.
2. Testes in-process (`TestClient`) — não substituem um pentest de instância
   implantada.
3. LIVE permanece **BLOCKED_BY_POLICY**: nada aqui autoriza nem habilita
   operação real.

## Arquivos

- `tests/test_auditoria_fin2_gates_financeiros.py` (novo, commit `3ee3fe0`)
