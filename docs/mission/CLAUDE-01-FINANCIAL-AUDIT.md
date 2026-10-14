# MISSÃO CLAUDE 01 — Auditoria independente do núcleo financeiro, auth e execução

- **UTC:** 2026-10-13
- **Branch:** `audit/claude-financial-security` (a partir do HEAD verificado `70b153d`)
- **Execução:** worktree + venv isoladas (editable install da worktree), para não
  colidir com o Qoder, que opera em paralelo em `feat/integracao-mestre-20261012`.
- **Regra:** nenhum gate é declarado PASS sem prova reexecutável. Nenhuma ordem
  real executada. Nada neste trabalho habilita LIVE.

---

## ATUALIZAÇÃO 2026-10-14 — HEAD verificado `30b8683` (pós-Qoder)

O agente Qoder concluiu sua missão. O estado do repositório mudou desde o
baseline `70b153d` desta auditoria (46 commits novos). Registro honesto:

- **PR #8 (single-owner fail-closed) foi INTEGRADO** à base
  `feat/integracao-mestre-20261012` (merge em 2026-10-13T18:54:07Z). A trava
  fixa (`SINGLE_OWNER_ENFORCED = True`, nenhuma flag reabre) é agora o contrato
  do produto, por decisão arquitetural do dono.
- **Login do dono com Argon2id presente** (`service/api/owner_credentials.py`,
  PR #10): a senha do dono deixou de viver em texto puro e passou a hash
  Argon2id + código de recuperação rotativo. Isso FORTALECE a autenticação
  (Responsabilidade 7) — mas é hash de SENHA DE LOGIN, não resolve o SEC-1.
- **A auditoria original (§1–§9 abaixo) cobre apenas o baseline `70b153d`.**
  Os módulos adicionados depois (motor global de saída `desk/saida.py`,
  copiloto `desk/piloto.py`, alertas `desk/canais_alerta.py`/`notificacao_viva.py`,
  escola `desk/escola.py`, maestro `desk/maestro.py`, salas `desk/salas.py`,
  laboratório quant `tradingagents/quant/*`, modo `desk/modo.py`) **NÃO estavam
  cobertos** e são auditados na seção **§10 — Auditoria do código novo**.

### Status dos achados P0 no HEAD `30b8683` (reverificado, NÃO resolvidos sem prova)

- **FIN-1 — AINDA PRESENTE (depois CORRIGIDO neste PR).** O docstring do
  `ccxt_bridge.py` na base final ainda afirmava que o modo LIVE foi "removido"
  e que "nenhuma configuração pode criar um cliente com endpoints reais" —
  falso. Correção e testes em §10.
- **SEC-1 — AINDA PRESENTE (depois CORRIGIDO neste PR).** `save_cfg` na base
  final seguia gravando as chaves de API em TEXTO CLARO no `config.json`
  (apenas `chmod 600`). O Argon2id protege só a senha de login, não as chaves
  armazenadas. Correção (cifragem em repouso + migração) em §10.

---

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

---

## §10 — Auditoria do código novo (HEAD `30b8683`) + correções P0

Escopo: módulos adicionados após o baseline `70b153d` e os P0 cruzados com a
auditoria independente do ChatGPT (commit por ele examinado: `4b83e16`).
Corretora SIMULADA em todos os testes de envio — nenhuma ordem real, nenhuma
credencial real, LIVE não habilitado.

### SEC-1 (P0) — CORRIGIDO: segredos cifrados em repouso

- **Causa raiz:** `service/app.py::save_cfg` gravava as chaves de API em TEXTO
  CLARO no `config.json` (só `chmod 600`).
- **Correção:** novo `tradingagents/security/secret_store.py` (`SecretStore`)
  cifra os segredos com Fernet (`cryptography`) em `secrets.<ns>.enc`. A
  chave-mestra (`secret.key`, 0600) é ENVELOPADA por DPAPI no Windows quando
  `win32crypt` está disponível, com fallback portátil (chave direta 0600) fora
  do Windows / sem pywin32 — serve Windows e Oracle Linux sem quebrar
  portabilidade e sem dependência obrigatória nova.
- `save_cfg` passou a rotear segredos declarados para o store (config.json fica
  só com config não-secreta); `load_cfg` sobrepõe os segredos decifrados.
- **Migração fail-safe:** `_migrar_segredos_plaintext` move segredos legados do
  config.json para o store, **valida o round-trip**, faz **backup**
  (`config.json.bak-pre-secret-store`) e só então reescreve o arquivo sem os
  segredos. Se a validação falhar, aborta e preserva tudo — nunca remove
  credencial sem recuperação provada.
- **Evidência:** `tests/test_secret_store_sec1.py` (12 testes): cifra em
  repouso (o valor não aparece no arquivo), recuperação por instância nova,
  isolamento por namespace, fail-closed em chave/arquivo corrompido, migração
  com backup, POST→GET mascarado e disco sem texto claro, idempotência.
- **Limitações:** no Oracle Linux headless sem keyring, a proteção efetiva da
  `secret.key` recai sobre permissões de arquivo (0600) — adequado para serviço
  de um único dono, porém não equivalente ao DPAPI; integrar Secret Service/
  keyring quando houver sessão é um reforço futuro. Segredos já em `os.environ`
  (modo legacy) seguem sendo herdados — é a configuração do próprio dono.

### FIN-1 (P0) — CORRIGIDO: documentação do CCXTBridge alinhada à implementação

- **Causa raiz:** o docstring afirmava que LIVE foi "removido" e que "nenhuma
  configuração pode criar um cliente com endpoints reais" — falso; o ramo
  `mode=="live"` existe e chama `create_order`.
- **Correção:** docstring reescrito para descrever a capacidade LIVE real,
  desligada por padrão e protegida por travas fail-closed, `BLOCKED_BY_POLICY`.
  **Implementação não alterada** (gate LIVE intocado, por política).
- **Evidência:** `tests/test_live_execution_blocked.py` (13 testes) prova as
  recusas sem autorização e que, no estado atual do produto, a rota legada
  `/v1/broker/order` responde 410 e o docstring não mais afirma remoção.

### FIN-2 / FIN-3 — auditoria adversarial do envio (corretora simulada)

- **Evidência:** `tests/test_fin_adversarial.py` (9 testes).
- **FIN-3 (resposta perdida):** `clientOrderId` determinístico comprovado
  (retry reusa o mesmo id → corretora deduplica); a tentativa é jornalizada
  **antes** da chamada de rede (recuperação por `clientOrderId` mesmo sem a
  resposta); em erro de rede o bridge **re-levanta** e **nunca** grava
  `submit_ok` — nada de sucesso inventado. O tratamento de estado UNKNOWN e a
  não-liberação de capital em timeout ambíguo já são cobertos por
  `tests/test_auditoria_blockers.py::TestTimeoutAmbiguoNaoLiberaCapital`.
- **FIN-2 (gates de autorização):** o gate de transporte
  (`assert_secure_for_capital`) é camada independente e incontornável,
  verificada antes de qualquer rede inclusive em testnet (estado vazio ou mark
  inseguro ⇒ recusa, corretora nunca é tocada). A cadeia do desk (dono, conta,
  estratégia, kill-switch, limites, reconciliação) é coberta por
  `test_auditoria_blockers.py`, `test_kill_switch*.py` e
  `test_reconciliation_state.py`.

### Demais módulos novos — cobertura e limitações (honestidade de estado)

| Área | Módulo(s) | Verificação nesta auditoria | Status |
| :-- | :-- | :-- | :-- |
| Copiloto / isolamento de ordens | `desk/piloto.py`, `desk/modo.py` | leitura + arquitetura: modo fixo `paper (simulado)`, LIVE bloqueado, autorização explícita do dono com gates; isolamento sustenta-se pela trava LIVE do bridge (provada) | VERIFICADO (arquitetura) + suíte |
| Motor global de saída | `desk/saida.py` | suíte `test_motor_de_saida_global.py`, `test_desk_saidas.py` | BASELINE (testes verdes) |
| Alertas + deduplicação | `desk/canais_alerta.py`, `desk/notificacao_viva.py` | suíte `test_centro_de_alertas.py`, `test_alertas_transparencia.py` | BASELINE |
| Escola / certificação | `desk/escola.py`, `desk/conhecimento.py` | suíte `test_desk_escola.py`, `test_service_escola.py` | BASELINE |
| Maestro / 10 traders | `desk/maestro.py`, `desk/salas.py` | suíte `test_desk_maestro.py`, `test_qoder_maestro_traders.py`, `test_salas.py` | BASELINE |
| Laboratório quant / champion-challenger | `tradingagents/quant/*` | suíte `test_quant_intelligence.py` | BASELINE |

**Limitação declarada:** "BASELINE" significa que a suíte de testes do próprio
módulo foi reexecutada e passou — NÃO uma auditoria adversarial dedicada como a
feita para SEC-1/FIN-1/2/3. Esses módulos permanecem como próximo alvo de
aprofundamento adversarial. Nada aqui declara o G1 ou qualquer gate como PASS.
