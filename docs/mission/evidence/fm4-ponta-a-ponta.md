# FM-4 (§5–§8) — prova viva ponta a ponta em instância isolada

**Data:** 2026-10-14 · **Branch:** `fix/instalador-atualizacoes-windows`
**Script:** `tools/fm4_prova_viva.py` · **Captura bruta:** `docs/mission/evidence/fm4/prova-viva.json`

## Método (e o que NÃO foi tocado)

- Sobe o app real (`uvicorn service.app:app`) numa **instância isolada**:
  porta livre própria + `DATA_DIR` temporário (receita do `tests/e2e/conftest.py`),
  `MULTI_TENANT_ENFORCED=false`, senha de prova `s3nh4-dz23-e2e`, autenticação
  por `Authorization: Bearer` (o login é 404 pelo portão G2 de dono único).
- **Hermeticidade de IA:** o processo filho roda com as envs de chave de TODOS
  os provedores do catálogo REMOVIDAS (`_ambiente_do_filho()` importa o CATALOGO
  e faz `pop`), pois a estação do dono tem chaves reais — a prova não pode sair
  à rede por acidente. `TRADINGAGENTS_LLM_PROVIDER/MODEL` também são removidos.
- `DESK_INTERVAL_SECONDS=15` **somente nesta instância de prova** (default do
  produto: 300) para observar o laço autônomo dentro da janela da prova.
- **A instalação do dono (porta 8000) não foi tocada.** O script encerra
  apenas o processo que ele mesmo criou (`finally: terminate`).
- Reprodução: `.venv/Scripts/python.exe tools/fm4_prova_viva.py`

## §5 — Mesa + escola: 10 traders ponta a ponta

| Passo | Resultado observado |
| --- | --- |
| Boot da mesa (`GET /v1/desk/status`) | HTTP 200; `fonte.dados` = fixture, aviso honesto ("provam a mesa, não o mercado"); `contabilidade.balanceado` = true |
| Ciclo síncrono (`POST /v1/desk/cycle`) | HTTP 200 — **"20 propostas, 2 admitidas, 2 entradas, 0 saídas, 0 vetos"**; ordens `ord-1`/`ord-2` `filled` (PAPER) |
| Agendar (`POST /v1/desk/scheduler/start`) | HTTP 200 — `iniciado: true`; recuperação: 0 reconciliadas, 0 incertas, posições BTC/USDT e ETH/USDT |
| Laço autônomo (sem navegador) | `rodando: true`, **ciclos 1→3, erros 0** dentro da janela da prova |
| 10 traders | **total 10** — 3 `homologado` (`paper_enabled`) + 7 `experimental` (`shadow`), cada um com `strategy`, `stage_label`, universo e `ultimo_no_trade` |
| Sala individual (`GET /v1/desk/traders/trader-trend/sala`) | HTTP 200 — mercado ROTULADO fixture ("prova a mesa, não o mercado"), portfólio com motivo honesto ("sem operações fechadas — nada a medir"), `modelo_ia: desligado (núcleo determinístico)` |

Ressalva honesta: o ciclo síncrono e o laço autônomo executam o MESMO
`run_once` do agendador; a prova cobre os dois caminhos, com dados de FIXTURE
determinísticos (2 entradas de teste), não o mercado.

## §6 — Dashboards (maestro + status de operação)

- `GET /v1/maestro/painel` HTTP 200: `mesa_iniciada: true`; **segregação
  invariante** — `mistura_modos: false`, `mistura_moedas: false`, subtotais
  `por_modo.paper.USDT`; **LIVE declarado `bloqueado: true`** com motivo
  ("negociação real permanece bloqueada..."); traders 10 (3/7); aviso
  "leitura consolidada — não opera, não certifica e não autoriza operação real".
- `GET /v1/status/operacao` HTTP 200: `operacao_real.estado` =
  **BLOQUEADA** ("auditoria de segurança em andamento"); gates nomeados
  (`kernel financeiro`, `segurança de dados`) com status `EM_AUDITORIA`.

## §7 — Copiloto manual (sem rota que emita ordem)

Fluxo completo numa instância real (todas HTTP 200 salvo onde indicado):

1. `PUT /v1/manual/modo {"ativo": true}` → ativo (anterior false).
2. `POST /v1/desk/cycle` **→ HTTP 409** — "modo Copiloto Manual ligado — a
   mesa autônoma não executa ordens..." (pin do bloqueio cruzado).
3. `POST /v1/manual/positions` (BTC/USDT compra 0.01 @ 100000, stop/alvos) →
   `criada: true`, id `mp_1b16a007efe9`.
4. **Idempotência real**: repetir o POST com a mesma `idempotency_key` →
   `criada: false`, MESMO id (nenhuma posição duplicada).
5. `POST .../events` (`entrada_parcial` 0.004 @ 101000) → `aplicado: true`;
   `GET .../positions/{id}` devolve posição + eventos.
6. `GET /v1/manual/monitor/health` → monitor ativo, 1 posição MONITORING,
   último tick registrado; `GET /v1/manual/sinais` HTTP 200 (análise
   determinística); `GET .../alerts` capturado (vazio — nenhum limite
   atingido; sem alerta inventado).
7. **Reversibilidade**: `PUT {"ativo": false}` → desligado; novo
   `POST /v1/desk/cycle` → HTTP 200 ("20 propostas, nenhuma operação: critérios
   não atendidos (18 abstenções)") — a mesa volta ao normal.

Contrato read-only por DESCOBERTA (não allowlist fixa): `tests/test_fm4_ponta_a_ponta.py`
varre `app.routes` por `/v1/manual*`, resolve cada endpoint ao AST de
`service/app.py` e prova: (a) nenhuma função manual chama
`create_order/cancel_order/close_position/submit/withdraw/transfer`;
(b) TODA rota manual exige `auth`. A descoberta cobre as 14 rotas — incluindo
`manual_sinais`, que as allowlists antigas NÃO pinavam (lacuna fechada aqui).

## §8 — IA: "configurado" ≠ "funcionando" (sem chave, honesto)

- `GET /v1/ai/provedores`: 9 provedores do catálogo, **todos
  `tem_chave: false`** (ambiente hermético); nenhum aparece como configurado.
- `POST /v1/ai/testar {"provedor": "openai"}` → `ok: false`,
  `estado: "sem_chave"`, detalhe "sem chave salva para o provedor
  (OPENAI_API_KEY)" — **nenhuma saída de rede sem credencial**.
- `GET /v1/ai/uso` → consumo zerado, custo parcial false, orçamento
  indefinido — sem número inventado.

## Ressalvas declaradas

- Dados de FIXTURE: provam a mesa (decisão, risco, contabilidade, laço),
  **não o mercado**.
- Alertas do copiloto vazios nesta janela (nenhum limite de preço atingido
  em 1 tick de monitor) — comportamento observado, não omitido.
- Nenhuma chamada de IA real foi feita (sem chave no ambiente de prova;
  o dono não autorizou consumo neste ciclo).
- `DESK_INTERVAL_SECONDS=15` vale só para a instância da prova; o default do
  produto segue 300 s.
