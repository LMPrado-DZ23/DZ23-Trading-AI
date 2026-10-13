# DZ23 Trading AI — TRADING GATES STATUS (MASTER V7)

Definição de gates conforme PROMPT MASTER V7 (FECHAMENTO TOTAL WINDOWS).
Regra do V7: **nenhum PASS sem comprovação** — cada linha cita o teste, run ou artefato que a sustenta.
Estados: PASS, PASS_LOCAL, IN_PROGRESS, BLOCKED_BY_EXTERNAL_DEPENDENCY, BLOCKED_BY_POLICY, FAIL.

| Gate | Descrição (V7) | Status | Evidência verificável |
| :--- | :--- | :--- | :--- |
| **G0** | Auditoria adversarial e linha de base | `PASS` | `docs/mission/TRADING-CHECKPOINTS.md` (C000–C006: repo, PRs #3/#5/#6/#7, CI, ambiente, matrizes, E2E real); o teste vermelho que o rewrite do workflow introduziu no CI do HEAD foi encontrado e corrigido (C006, item 3) |
| **G1** | Segurança financeira crítica (F3–F6, K1–K6, fail-closed) | `PASS_LOCAL` | `tests/test_paper_financial_state.py`, `tests/test_reconciliation_state.py`, propriedades de ledger/FeeEngine; suíte completa 2131 passed no HEAD `2562bd6`; CI push run `38436932376` success |
| **G2** | Single-owner real (superfície SaaS desligada, dono nunca trancado) | `PASS` | `tests/test_single_owner_modo_windows.py` (3/3): signup/webhooks/login 404 por padrão, prefixo `/v1/auth/oauth` inteiro bloqueado (fecha vazamento do provedor microsoft), plano vencido não bloqueia o dono; CI da branch `fix/g2-single-owner-20261013` run `38429848704` success. **Prova E2E nova (C006):** o dono entra no painel por navegador real — tela "Entrar na sua mesa" → `/v1/auth/me` com Bearer → "Visão geral" READY/ADMIN; contratos em `test_frontend_contract.py` travam o fluxo |
| **G3** | Windows executável instalável, homologado | `IN_PROGRESS` | Pipeline de build reescrito em `.github/workflows/windows-release.yml` (commit `e3e6239`): Inno Setup via Chocolatey, PyInstaller via `uv run --with`, artefatos `exe+zip+SHA256SUMS` por push. **Build verde:** run `38437252278` success; artefato `dz23-windows-x64` baixado e integridade conferida (Setup `9ce9c33a…` ✓, zip `148fee9a…` ✓). **Pendente:** instalação limpa real (10/11 x64), shortcuts, sem console, porta ocupada, uninstall preservando dados — executar com o artefato do **próximo build** (já com SHA256SUMS padrão e `PrivilegesRequired=lowest`) |
| **G4** | Configuração visual de APIs de IA externas | `PASS_LOCAL` | Painel PT-BR (provedor/modelo/chave/custos/status), `docs/mission/AI-PROVIDER-MATRIX.md`, rotas `/v1/config` + `/v1/quant/status` (latência/saúde da IA de contexto); testes `test_quant_intelligence.py` (painel + ciclo). **Prova com credencial real do dono:** `BLOCKED_BY_EXTERNAL_DEPENDENCY` (sem credenciais autorizadas na sessão) |
| **G5** | Corretoras e salas | `PASS_LOCAL` | `docs/mission/CONNECTOR-MATRIX.md` + `service/personal_exchange_capabilities.py` (capacidades com fonte, estados NOT_SUPPORTED/BLOCKED/PAPER_SIMULATED, nunca PASS falso), testes de catálogo/conectores na suíte; conexões testnet/demo reais: `BLOCKED_BY_EXTERNAL_DEPENDENCY` |
| **G6** | Agência/quant completos | `PASS_LOCAL` | 10 traders + `MarketRegimeSupervisor` (`tests/test_qoder_ten_traders.py`, `tests/test_desk_multiagente.py`), camada quant (`tradingagents/quant/`, `tests/test_quant_intelligence.py` 40 testes), walk-forward/leakage (`tests/test_research.py`), governança "IA nunca emite ordem" (teste estrutural AST). Pendente de verificação: investigação das 3 operações Binance via logs reais |
| **G7** | UX premium para leigos | `IN_PROGRESS` | Painel PT-BR em `service/static/index.html` (seções principais + botão "Camada quant/IA"); testes de contrato do frontend verdes. **E2E em navegador real já executado (C006)** — encontrou e corrigiu o defeito crítico do login do dono e o iframe do Trading Floor; fluxo do dono provado por navegador (login → painel → floor → screenshot). **Suíte E2E versionada criada (C007):** `tests/e2e/` (Playwright + uvicorn real) com 5 testes verdes cobrindo login do dono, 8 páginas do menu, formulários, validação, floor embutido, governança quant e contratos 4xx/401 + zero 5xx na navegação; screenshots em `docs/mission/evidence/e2e/` |
| **G8** | CI, E2E, distribuição, homologação | `IN_PROGRESS` | CI Linux matriz 3.10–3.13 verde (runs `38422314837`, `38408458373`, `38436932376`); suíte local **2136 passed / 4 skipped** no working tree C006 (2131 passed no HEAD `2562bd6`); build Windows gerando artefato de CI (run `38437252278` success). CI do HEAD `e3e6239` ficou **vermelho** (teste do workflow desatualizado, run `38437252181`) — corrigido no batch C006. CI do `0eb40a2` (run `38439685290`): testes py3.12 verdes, ruff vermelho por `I001` num teste do launcher — corrigido no C007. **E2E Playwright versionado criado** (`tests/e2e/`, 5 passed com navegador real; skip explícito sem playwright). **Pendente:** instalação limpa com o artefato do CI, PAPER ponta a ponta com reinício/reconciliação documentados como evidência |
| **G9** | LIVE (dinheiro real) | `BLOCKED_BY_POLICY` | Fora do escopo. `LIVE_TRADING=false` / `REAL_MONEY_EXECUTION=false` fail-closed; ativação exige decisão humana nova e explícita. Não mexer |

## Correções de fidelidade desta versão

- O documento anterior (MASTER V5) declarava `G8 = PASS` citando testes unitários e CI Linux — **PASS sem comprovação de instalação Windows**, corrigido para `IN_PROGRESS` conforme exigência do V7.
- A numeração V5 (G3=Exchange, G8=Installer) foi substituída pela numeração V7 (G3=Installer, G5=Corretoras).

## Itens abertos rastreados

1. **G3:** rodar homologação de instalação limpa nesta estação Windows 11 com o artefato do CI (instalar → healthz → shortcut → fechar → reinstalar → uninstall preservando `%APPDATA%`).
2. **G7/G8:** criar `tests/e2e/` com Playwright dirigindo o painel real (menu, gráficos, formulários, 4xx/5xx).
3. **G6:** investigar as 3 operações Binance com logs reais (não ampliar frequência artificialmente).
4. **G4/G5:** prova com credenciais reais do dono (IA e corretoras) quando autorizadas — até então permanece BLOCKED_BY_EXTERNAL_DEPENDENCY.
5. **G3:** assinatura de código somente se certificado autorizado; hash SHA256 já no pipeline.

*Última atualização: 2026-10-13 — commit C006 sobre `e3e6239`, branch `feat/integracao-mestre-20261012`, PR #7.*
