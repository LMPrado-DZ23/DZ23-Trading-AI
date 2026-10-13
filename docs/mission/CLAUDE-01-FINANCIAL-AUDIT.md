# MISSÃO CLAUDE 01 — Auditoria independente do núcleo financeiro, auth e execução

- **UTC:** 2026-10-13
- **Branch:** `audit/claude-financial-security` (a partir do HEAD verificado `70b153d`)
- **Execução:** worktree + venv isoladas (editable install da worktree), para não
  colidir com o Qoder, que opera em paralelo em `feat/integracao-mestre-20261012`.
- **Regra:** nenhum gate é declarado PASS sem prova reexecutável. Nenhuma ordem
  real executada. Nada neste trabalho habilita LIVE.

## 0. Evidência de base (reexecutável)

```bash
# na worktree isolada:
python -m pytest tests/test_auditoria_blockers.py tests/test_live_execution_blocked.py \
  tests/test_fee_*.py tests/test_taxa_em_outra_moeda.py tests/test_valoracao_*.py \
  tests/test_valuation_fingerprint.py tests/test_contabilidade_multimoeda.py \
  tests/test_reserva_*.py tests/test_deficit_*.py tests/test_modelo_capital.py \
  tests/test_verdade_unica_capital.py tests/test_compensacao_vinculada.py \
  tests/test_settlement_atomico.py tests/test_recovery_settlement.py \
  tests/test_prova_de_resolucao.py tests/test_reconciliacao_adversarial.py \
  tests/test_reconciliation_state.py tests/test_relatorio_reconciliacao.py \
  tests/test_fills_reconciliacao.py tests/test_ordens_orfas.py \
  tests/test_kill_switch*.py tests/test_crash_restart.py tests/test_critical_safety.py \
  tests/test_paper_financial_state.py tests/test_guard_*.py tests/test_broker_*.py \
  tests/test_security_fase0*.py -q
# resultado: 636 passed
```

Novo artefato desta auditoria: `tests/test_live_execution_blocked.py` (9 testes).

## 1. Matriz dos bloqueadores F3–F6 / K1–K6

| # | Bloqueador | Dano se ignorado | Verificado por | Status |
| :-- | :-- | :-- | :-- | :-- |
| **F3** | Valoração por ocorrência | uma cotação aplicada a taxas de horários diferentes | `test_valoracao_exactly_once.py`, `test_valoracao_taxa.py` | VERIFICADO (testes verdes) |
| **F4** | Liquidação do passivo funcional `LIABILITY:FEE_FUNDING` | passivo nunca zera | `test_fee_patch_f1f2.py`, `test_fee_componentes.py`, `test_valoracao_exactly_once.py` | VERIFICADO |
| **F5** | Taxa em base currency com lastro | taxa em BTC sem origem | `test_taxa_em_outra_moeda.py`, `test_fee_multi_moeda.py`, `test_contabilidade_multimoeda.py` | VERIFICADO |
| **F6** | Parser único na reconciliação | dois caminhos divergem | `test_fee_passagem_unica.py`, `test_fee_pipeline.py` | VERIFICADO |
| **K1** | `expires_at` (reserva expirada não financia) | reserva morta financia ordem | `test_crash_restart.py`, `test_reserva_identificada.py` | VERIFICADO |
| **K2** | `mandate_version` | reserva de v1 financia ordem sob v2 | `test_fills_reconciliacao.py`, `test_reconciliacao_adversarial.py`, `test_reserva_identificada.py` | VERIFICADO |
| **K3** | `reserved_amount=0` não é "pular validação" | truthy usado como bypass | `test_deficit_fonte_unica.py`, `test_deficit_reserva.py`, `test_caminho_unico.py` | VERIFICADO |
| **K4** | Boot freshness do SAFE | relatório pré-restart autoriza | `test_crash_restart.py`, `test_auditoria_blockers.py::TestEstadoFinanceiroSobreviveAoReinicio` | VERIFICADO |
| **K5** | `risk_status` no relatório | reconciliação diz SAFE sem olhar risco | `test_reconciliation_state.py`, `test_relatorio_reconciliacao.py`, `test_incidentes_risco.py` | VERIFICADO |
| **K6** | Fill `RECEIVED/APPLYING/COMMITTED` (commit durável) | fill marcado visto antes de contabilizar | `test_fills_reconciliacao.py`, `test_settlement_atomico.py`, `test_ordens_orfas.py`, `test_auditoria_blockers.py::TestTimeoutAmbiguoNaoLiberaCapital` | VERIFICADO |

> "VERIFICADO" significa: existe teste que reexecutei e passou cobrindo o
> comportamento. NÃO é declaração de PASS do gate G1 inteiro — ver §5
> (riscos residuais) para o que ainda carece de teste adversarial dedicado.

## 2. Responsabilidade 3 — fills duplicados, parciais, atrasados e fora de ordem

| Cenário | Evidência | Status |
| :-- | :-- | :-- |
| Timeout ambíguo NÃO libera capital (ordem pode ter chegado) | `test_auditoria_blockers.py::TestTimeoutAmbiguoNaoLiberaCapital` (parametrizado em 6 classes de erro) | VERIFICADO |
| Erro que PROVA não-envio libera capital | mesmo arquivo, `test_rejection_releases_capital_as_it_should` | VERIFICADO |
| Fills parciais / reconciliação | `test_fills_reconciliacao.py` | VERIFICADO |
| Ordens órfãs | `test_ordens_orfas.py` | VERIFICADO |
| Settlement serializado e idempotente | `test_settlement_atomico.py` | VERIFICADO |
| Idempotência por `clientOrderId` determinístico | `tradingagents/brokers/ccxt_bridge.py::client_order_id` + `test_broker_ccxt.py` | VERIFICADO |

## 3. Responsabilidade 4 — timeouts, idempotência, concorrência, recuperação

| Cenário | Evidência | Status |
| :-- | :-- | :-- |
| Estado financeiro sobrevive a restart; UNKNOWN ⇒ bloqueia novo risco | `test_auditoria_blockers.py::TestEstadoFinanceiroSobreviveAoReinicio`, `test_crash_restart.py` | VERIFICADO |
| Freio de perda diária vem do livro (não de chamada manual) e sobrevive a restart no mesmo dia | `test_auditoria_blockers.py::TestFreioDiarioVemDoLivro` | VERIFICADO |
| Kill switch persistente, fail-closed, liberar exige frase + reconciliação | `test_auditoria_blockers.py::TestParadaDeEmergenciaSobreviveAoReinicio`, `test_kill_switch*.py` | VERIFICADO |
| Mutação de `os.environ` sempre restaurada (concorrência de credenciais) | `test_auditoria_blockers.py::TestCredencialDeInquilinoNaoVazaPorAmbiente` | VERIFICADO |
| Concorrência multiprocess do ledger | **RISCO RESIDUAL** — provado intra-processo; entre processos é `BLOCKED_BY_ARCHITECTURE` até `UNIQUE(fee_component_id)` em Postgres (ver `docs/finalization/BLOCKERS.md`) | ABERTO |

## 4. Responsabilidade 5 — caminho `mode=live` (execução real)

**Achado FIN-1 (ALTO) — docstring falso.** `tradingagents/brokers/ccxt_bridge.py`
afirma no cabeçalho que LIVE foi "removido" e que "nenhuma configuração pode
criar um cliente com endpoints reais". **É falso**: existe um ramo `mode ==
"live"` funcional. Causa raiz: documentação não acompanhou o código.

**Achado FIN-2 (MÉDIO) — LIVE ativável por ambiente.** A execução real É
possível se todas estas forem fornecidas: `TRADINGAGENTS_BROKER_MODE=live`,
`LIVE_TRADING_CONFIRM` igual à frase exata, chaves reais, `LIVE_MAX_ORDER_VALUE>0`
e `LIVE_MAX_DAILY_LOSS>0`, mais o gate de capital. É multi-travado e nenhuma
rota anônima o aciona — porém a política G9 exige "nova decisão humana explícita
fora da missão", um portão mais forte que uma variável de ambiente.

**Prova de que SEM autorização a execução é recusada:** novo
`tests/test_live_execution_blocked.py` exercita, sem rede, as recusas
fail-closed (modo inválido, sem frase, frase errada, sem chaves, sem teto de
ordem, sem teto diário) e prova que o gate de capital barra todo envio em
qualquer modo antes de tocar a rede. **9/9 verdes.**

**Ação:** conforme limite da missão, **nada alterado** no gate LIVE. Correções
recomendadas (decisão humana): corrigir o docstring e/ou adicionar trava de
código fail-closed recusando `mode=live` até liberação explícita; registrar em
`TRADING-DECISIONS.md`. (`tradingagents/mt5/adapter.py` lê o mesmo
`LIVE_TRADING_CONFIRM` — mesma recomendação.)

## 5. Responsabilidade 6 — single-owner (SaaS/billing/signup/admin)

**Entregue em PR separado (#8, branch `fix/g2-single-owner-20261013`).** Para
não duplicar nem conflitar, esta auditoria NÃO repete a correção; referencia-a.
Resumo: superfície SaaS fechada fail-closed (404 incondicional), multi-tenant
forçado off, portão de plano (402) removido, prova adversarial em
`tests/test_single_owner_g2.py`. Ver `docs/mission/G2-SINGLE-OWNER-AUDIT.md`.

## 6. Responsabilidade 7 — autenticação das rotas do dono

| Propriedade | Evidência | Status |
| :-- | :-- | :-- |
| Rotas sensíveis exigem `Depends(auth)`; sem/credencial errada ⇒ 401 | `test_service_leigo.py` (lock de força-bruta, headers), `test_single_owner_g2.py` (acesso nunca aberto) | VERIFICADO (no PR #8) |
| Força-bruta: 5 erros/IP ⇒ 15 min | `test_service_leigo.py::...five_wrong_tries_lock_the_ip` | VERIFICADO |
| `X-Forwarded-For` ignorado sem proxy confiável | `test_service_leigo.py::...forwarded_for_is_ignored...` | VERIFICADO |
| **Cobertura explícita de authz por rota** (cada rota do dono tem teste de 401) | **RISCO RESIDUAL** — há cobertura ampla, mas não um inventário rota-a-rota de autorização | PARCIAL |

## 7. Responsabilidade 9 — armazenamento local de credenciais

**Achado SEC-1 (MÉDIO/ALTO conforme ameaça).** `service/app.py::save_cfg` grava
`config.json` com as chaves de API/segredos **em texto claro**, protegido apenas
por `os.chmod(path, 0o600)`. No Windows (NTFS) o modo POSIX 600 não é aplicado
como em Unix — a proteção efetiva depende das ACLs herdadas do diretório do
usuário. A API mascara segredos na exibição (bom), mas o repouso é plaintext.

**Recomendação (não implementada aqui; alinhada ao G2 amplo):** proteger
segredos via DPAPI / Windows Credential Manager; documentar threat model.
Severidade depende do modelo de ameaça (acesso local ao disco do dono).

## 8. Matriz de severidade (resumo)

| ID | Severidade | Área | Causa raiz | Correção proposta | Teste/Evidência | Risco residual |
| :-- | :-- | :-- | :-- | :-- | :-- | :-- |
| FIN-1 | Alto | mode=live | docstring desatualizado vs código | corrigir docstring / trava de código | `test_live_execution_blocked.py` | enganar operador sobre capacidade real |
| FIN-2 | Médio | mode=live | LIVE ativável por env | portão mais forte que env (decisão humana) | idem | ativação real por configuração sem revisão |
| SEC-1 | Médio/Alto | credenciais | segredos plaintext em `config.json` | DPAPI/Credential Manager | leitura de `save_cfg` | vazamento com acesso local ao disco |
| ARCH-1 | Médio | ledger multiprocess | sem `UNIQUE(fee_component_id)` | constraint em Postgres | `BLOCKERS.md` | duplo uso de componente entre processos |
| AUTHZ-1 | Baixo/Médio | auth | sem inventário rota-a-rota | teste de 401 por rota | parcial | rota nova sem auth passar despercebida |

Nenhum destes foi corrigido nesta branch (auditoria + prova de recusa de LIVE
apenas), exceto o conjunto single-owner, entregue no PR #8.

## 9. Gates — veredito honesto

| Gate | Veredito desta auditoria |
| :-- | :-- |
| G1 (ledger/fees/risco/fail-closed) | **Forte, mas NÃO declaro PASS**: F3–F6/K1–K6 têm teste verde; resta concorrência multiprocess (ARCH-1) e inventário de authz. |
| mode=live | Recusa sem autorização **provada**; ativável por env é achado aberto (FIN-1/FIN-2). LIVE segue `BLOCKED_BY_POLICY`. |
| Single-owner (item 6) | Entregue e provado no **PR #8**. |
| Credenciais | Achado SEC-1 aberto. |

Continua como trabalho iterativo: testes adversariais dedicados por bloqueador,
inventário de authz rota-a-rota e correções SEC-1/FIN-1 sob decisão humana.
