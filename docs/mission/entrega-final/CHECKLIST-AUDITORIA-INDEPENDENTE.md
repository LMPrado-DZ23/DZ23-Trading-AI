# DZ23 TRADING AI — CHECKLIST DE AUDITORIA INDEPENDENTE (V8.1 §13.14)

> Para os auditores independentes (**ChatGPT + Claude**), conforme desenho da missão (V8.1 §13: "O Qoder não
> poderá certificar a própria entrega como financeiramente aprovada"). Repositório:
> `https://github.com/LMPrado-DZ23/DZ23-Trading-AI` · Branch da RC: `fix/instalador-atualizacoes-windows` ·
> Versão `0.5.0` · Data: 2026-10-14.

## 1. Como auditar (roteiro de 30 minutos + roteiro profundo)

**Superfície (30 min):** leia, nesta ordem, e compare afirmações × evidências:
1. `docs/mission/entrega-final/MATRIZ-REQUISITOS-V8.md` — cada linha aponta para o teste e a evidência.
2. `docs/mission/entrega-final/RELATORIO-HOMOLOGACAO-V8.md` — placares, falhas corrigidas, riscos, dependências.
3. `docs/mission/TRADING-MISSION-STATE.md` + `TRADING-GATES.md` + `TRADING-CHECKPOINTS.md` — estado por fase.
4. `docs/mission/WINDOWS-RELEASE-EVIDENCE.md` — itens 1–22 no aplicativo instalado.
5. `docs/mission/evidence/` — prova crua (logs, capturas, placares).

**Profundo (reprodução):** execute você mesmo (não confie em captura):

```bash
# 1) Suíte completa (esperado: 3001 passed, 6 skipped condicionais, 71 subtests, exit 0)
uv run pytest -q

# 2) E2E com navegador real (esperado: 26 passed)
uv run --with playwright pytest tests/e2e -q

# 3) Lint (esperado: limpo)
uv run ruff check .

# 4) Auditoria adversarial dos gates financeiros (esperado: verde)
uv run pytest tests/test_fin2_* tests/test_fin3_* tests/test_kernel_* tests/test_fees_* -q

# 5) Pins de "não é caminho de ordem" no copiloto (AST + grep) (esperado: verde)
uv run pytest tests/test_copiloto_sinais.py tests/test_decisao_mesa.py -q

# 6) Artefato Windows: push na branch → CI "Windows Release" → job aceite-bundle (13/13)
#    ou rode scripts/homolog_aceite_td5_bundle.py contra um ZIP baixado (ambiente ISOLADO)
```

## 2. Afirmações centrais para checar (e como falsificá-las)

| Afirmação | Como tentar falsificar |
| :--- | :--- |
| "Copiloto Manual nunca cria/cancela ordens" | Procure `create_order`/`cancel_order`/ccxt/rede em `copiloto_sinais.py` e na rota `manual_*` de `service/app.py`; AST pinado em `tests/test_copiloto_sinais.py`. Tente achar qualquer rota `/v1/manual/*` que toque broker/OMS. |
| "LIVE bloqueado em profundidade" | `tests/test_fin2_*` (matriz adversarial), selo de código obrigatório (`e66f2b7`), REAL `disabled` na UI, `SINGLE_OWNER_ENFORCED` no código, `killswitch.py`. Tente forjar `modo=live` por API — deve recusar. |
| "Nenhuma posição executada por intenção" | `ledger.py`: posição nasce só de `fill_*`; testes de alertas exigem evidência. Procure atalhos que marquem posição sem fill. |
| "Sharpe/Sortino honestos (não inventados)" | `sala_trader.indices_de_risco`: abaixo do mínimo ⇒ `None` + motivo; desvio zero ⇒ indefinido. Veja `TestIndicesDeRisco`. Tente forçar números pequenos e confira `None`. |
| "Decisão do ciclo OPERAR/AGUARDAR/BLOQUEAR/SUSPENDER real" | `decisao.py` + `GET /v1/desk/status.decisao`; ladeira de precedência testada em `tests/test_decisao_mesa.py`. Tente contradizer: mesa pausada deve dar BLOQUEAR com motivo; ledger desbalanceado deve dar SUSPENDER. |
| "Escola não atravessa corretora" | `escola.py`/`salas.py`: chave `corretora|conta|mercado|modo`; certifique em Binance e veja não valer na OKX (`tests/test_escola_*`). |
| "Promoção automática só em PAPER" | `evolucao.py`: modo ≠ paper ⇒ recusa; LIVE jamais. `test_evolucao_*`. |
| "Push honesto (não finge entrega)" | `push_web.py` + Despachante: canal não configurado informa; nada de "corretora executou" sem fill. |
| "Segredos protegidos" | `segredos_em_repouso.py` (DPAPI), export sem segredos, gitleaks sem allowlist no CI. Procure qualquer segredo em claro em logs/fixtures. |
| "Suíte sem skip que esconde falha" | Os 6 skips são condicionais de ambiente (§3 abaixo) — confirme rodando nos ambientes onde o skip NÃO ocorre (Linux, com `DATABASE_URL`, com dependências opcionais). |

## 3. Skips declarados (verificáveis)

| Skip | Condição | Verificação |
| :--- | :--- | :--- |
| `test_modo_da_mesa.py` postgres | sem `DATABASE_URL` | rodar com Postgres real (job `postgres` do CI) |
| `test_desk_backup.py` POSIX 600 | Windows/NTFS | rodar em Linux |
| `test_bedrock_provider.py` | sem `langchain_aws` | instalar o extra |
| `test_quant_intelligence.py` | sem `lightgbm` | instalar o extra |
| `tests/e2e/test_panel_e2e.py` + `tests/e2e/test_aceite_td5.py` (módulos inteiros) | `playwright` ausente na venv base | `uv run --with playwright pytest tests/e2e -q` → **26/26**; job `aceite-bundle` do CI |

## 4. O que o executor DECLARA que NÃO fez (para você verificar se é verdade)

1. **Não** publicou tag/Release; **não** fez deploy; **não** usou capital real; **não** ativou LIVE.
2. **Não** leu/imprimiu/copiou `.env`, tokens ou chaves; **não** usou chave fictícia para "homologar".
3. **Não** tocou a instalação pessoal do dono (porta 8000) nem o worktree `dz23-audit-worktree`.
4. **Não** implementou: WhatsApp oficial, adaptadores IBKR/Alpaca/B3 (decisão registrada, nenhum ativado),
   Ollama, assinatura de código, deploy Oracle.
5. **Não** certifica a própria entrega como financeiramente aprovada — esta auditoria existe justamente para isso.

## 5. Perguntas que a auditoria deve responder

1. Os gates financeiros (autorização, limites, kill-switch, reconciliação, posse exclusiva) são suficientes
   para um piloto futuro com capital pequeno? O que endurecer antes?
2. A distinção PAPER/TESTNET/LIVE é respeitada em todas as curvas, gráficos e alertas (nunca somados)?
3. A abstenção inteligente está coerente com a filosofia do proprietário (saber não operar)?
4. Os indicadores (Sharpe/Sortino/expectativa/drawdown) são estatisticamente honestos e documentados?
5. Existe algum caminho não mapeado para ordem real? (ver `auditoria-4b83e16-fin1-selo-live.md`)
6. Os riscos residuais de B §6 estão completos? Falta algum?

## 6. Evidências-chave por área

| Área | Arquivo de evidência |
| :--- | :--- |
| Windows instalado (itens 1–22) | `docs/mission/WINDOWS-RELEASE-EVIDENCE.md` |
| Aceite no bundle (13/13) | `docs/mission/evidence/v8-td5-aceite.md` + `v8-td5-exe/` |
| Matriz do instalador | `docs/mission/evidence/e2e/matriz-38512611447/` |
| Segurança/financeiro (por achado) | `docs/mission/evidence/auditoria-4b83e16-*.md` |
| Evolução autônoma | `docs/mission/evidence/e5-evolucao-autonoma.md` |
| Multimercado | `docs/mission/evidence/e6-multimercado.md` |
| IA/config | `docs/mission/evidence/v8-s6-configuracao-ia.md` + `v8-smart-setup.md` |
| PWA/push | `docs/mission/evidence/v8-pwa-push.md` |
| Painel global / salas / gráficos / copiloto | `v8-painel-global.md`, `v8-sala-trader.md`, `v8-td3-graficos.md`, `v8-copiloto-dashboard.md` |
| Notificações (causa-raiz) | `v8-s5-notificacoes-repetidas.md` |

> **Regra de ouro da auditoria:** capture você mesmo (`rg`, `pytest`, `git log`, artefatos do CI). Se uma
> afirmação desta entrega não puder ser reproduzida, ela deve ser tratada como não-verificada — e isso deve
> ser reportado. A entrega foi construída para suportar exatamente esse escrutínio.
