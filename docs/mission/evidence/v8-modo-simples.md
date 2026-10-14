# V8 — SS-5: Interface Simples (padrão) + Controle Avançado — evidências

Diretriz definitiva de UX do dono (verbatim): *"Eu quero apenas cadastrar as
APIs necessárias, escolher DEMO, REAL ou COPILOTO DE SINAIS e apertar PLAY."*
E a regra fundamental: *"SIMPLIFICAR NÃO SIGNIFICA REMOVER FUNCIONALIDADES."*

Data das provas: 2026-10-14. Branch `fix/instalador-atualizacoes-windows`.
Arquivos: `service/static/index.html` (painel), `tests/test_modo_simples.py`
(novo, 18 testes), `tests/e2e/test_panel_e2e.py` (1 teste e2e novo + correção
de defeito latente da suíte).

## Requisito → implementação → evidência

| Requisito (diretriz do dono) | Implementação | Evidência |
|---|---|---|
| Wizard em 3 passos: CONECTAR → MODO → PLAY | Nova página **Início** (`<section id="page-inicio">`) dentro de `<main class="content">`, 3 cards: **1 · CONECTAR** (corretora + chaves + IA opcional), **2 · MODO** (DEMO / REAL / COPILOTO DE SINAIS), **3 · PLAY** (Ligar/Parar + estado da mesa + pendências) | `tests/test_modo_simples.py::TestPaginaInicioTresPassos`; e2e `test_modo_simples_assistente_conectar_modo_play`; captura `v8-modo-simples/ms-simples-inicio.png` |
| Modo Simples é o PADRÃO (sem tocar em configuração) | `uiModoAtual()` = `simples` salvo se `localStorage['dz23_ui_modo']==='avancado'`; `renderNav()` filtra para `['inicio','overview','manual']`; `enterApp()` entra direto no Início | pin `?'avancado':'simples'` + e2e `test_login_mobile` (título "Início") e `test_modo_simples_assistente_conectar_modo_play` (3 botões na sidebar) |
| "Simplificar ≠ remover": Modo Avançado mostra TUDO | `renderNav()` no avançado usa o array `pages` COMPLETO (8 páginas: inicio, overview, config, analysis, mesa, manual, floor, risk — todas as antigas preservadas) | pin `test_nada_foi_removido_do_menu_antigo` (PAGINAS_ANTIGAS ⊆ ids) + e2e: contagem 3 ↔ 8 ao alternar, verificada AO VIVO no navegador |
| REAL continua BLOQUEADO nos DOIS modos | `<input id="iniModoReal" disabled>` + `<small class="warn">BLOQUEADO</small>` + texto "até homologação e autorização explícita do proprietário"; o toggle de modo NÃO pode tocar nisso | pins `test_real_bloqueado_com_motivo_e_sem_habilitacao` e `test_avancado_nao_mexe_no_real`; e2e confirma `#iniModoReal` disabled |
| PLAY/PAUSAR = MESMA mesa (nenhum sistema paralelo) | Botões chamam `inicioSched('start'/'stop')` → `POST /v1/desk/scheduler/{start\|stop}` (rota existente da mesa); estado lido de `/v1/desk/status` | pin `test_play_e_parar_usam_as_mesmas_rotas_da_mesa`; e2e: PLAY → toast "LIGADA" + "RODANDO"; PAUSAR → "PARADA"; captura `ms-play-pausar.png` |
| CONECTAR grava pelas MESMAS chaves de `/v1/config` | `inicioConectar()` monta `{TRADINGAGENTS_EXCHANGE, EXCHANGE_API_KEY, EXCHANGE_API_SECRET, <PROVEDOR>_API_KEY}` e faz `POST /v1/config`; campos sensíveis limpos do DOM após enviar | pin `test_conectar_salva_pelas_mesmas_chaves_de_config` |
| PAPER identificado como SIMULADO | Estado da mesa exibe "PAPER (simulado), dinheiro real bloqueado."; modo DEMO idem | pin `test_demo_e_paper_marcados_como_simulado`; e2e lê a linha de estado `#iniMesaEstado` |
| COPILOTO DE SINAIS nunca envia ordens | Card do modo: "COPILOTO DE SINAIS … nunca envia ordens"; escolher copiloto leva a "Minhas Operações" (acompanhamento) | pin `test_copiloto_avisa_que_nunca_envia_ordens`; e2e asserta o texto na página |
| Limites são de código determinístico; dados vencidos → recusa e avisa | Texto explícito nos cards MODO (limites de código, nunca da IA) e PLAY ("recusa e avisa") | pin `test_limites_sao_deterministicos_texto_na_tela` |
| Preferência de interface é POR DEVICE, puramente visual | `uiModoSet()` grava `localStorage` e re-renderiza nav; **nunca chama `api(`** (nenhuma rota); trocando para simples estando em página oculta, leva ao Início | pins `test_toggle_e_puramente_visual_nunca_fala_com_o_servidor` e `test_alternar_para_simples_sai_de_pagina_oculta` |
| Assistente lê estado REAL (sem inventar) | `inicioLoad()` consome `/v1/config`, `/v1/desk/status` e `/v1/setup/estado?perfil=pronto`; pendências reais do Smart Setup exibidas | pin `test_assistente_le_estado_real_sem_inventar` |
| Página Início dentro da coluna de conteúdo (regressão do div órfão) | `<section id="page-inicio">` inserida antes de `page-overview` **dentro** de `<main class="content">` | pin `test_pagina_inicio_dentro_da_coluna_de_conteudo` + `TestRenderHttp` (rota autenticada `/app` contém os 3 ids) |
| Nada de backend novo | ZERO mudanças em `service/app.py` — só rotas existentes reutilizadas | `git diff` do commit: apenas `index.html` + testes + docs |

## Provas frescas (comandos e resultados exatos)

1. **RED (antes da implementação):** `pytest tests/test_modo_simples.py -q`
   → **`17 failed, 1 passed`** (a única que passou é a guarda intencional
   `nada_foi_removido_do_menu_antigo`, que protege a regra "simplificar ≠
   remover" desde o primeiro momento).
2. **GREEN focado:** `pytest tests/test_modo_simples.py tests/test_ia_painel_static.py -q`
   → **`45 passed in 1.50s`** (18 SS-5 + 27 estáticos do painel).
3. **E2E navegador real (Playwright + uvicorn real):**
   `uv run --with playwright --python .venv/Scripts/python.exe python -m pytest tests/e2e -v`
   → **`13 passed in 30.81s`** (12 anteriores + `test_modo_simples_assistente_conectar_modo_play`).
4. **Homologação AO VIVO em instância temporária isolada** (porta 8786,
   `DATA_DIR` em temp, ambiente **sem** chaves de IA reais — ver Limitações;
   app do dono em `:8000` intocado):
   - Login → painel abre no **Início**; sidebar com 3 botões
     (▶ Início, ⌂ Visão geral, ▣ Minhas Operações); seletor "Simples".
   - Estado da mesa: `"Mesa autônoma: PARADA · 0 ciclos · dados: fixture —
     PAPER (simulado), dinheiro real bloqueado."` + pendências reais do
     Smart Setup listadas ("Nada essencial pendente").
   - **PLAY AO VIVO:** clique → toast → `{"estado":"Mesa autônoma: RODANDO · 0 ciclos …"}`.
   - **PAUSAR AO VIVO:** clique → `{"estado":"… PARADA · 1 ciclos …","toast":"Mesa PARADA"}`
     (1 ciclo real executou no intervalo — fiação de verdade, não botão morto).
   - **Toggle de modo AO VIVO:**
     `{"nSimples":3,"nAvancado":8,"labels":["inicio","overview","config","analysis","mesa","manual","floor","risk"],"nVolta":3,"modo":"simples"}`
     — todas as 8 páginas de sempre no avançado, 3 no simples.
   - **Console do navegador: ZERO mensagens** (`list_console_messages` vazio —
     nenhum erro de JS nas duas interações).
   - Capturas: `ms-simples-inicio.png`, `ms-play-pausar.png`,
     `ms-modo-avancado.png` (nesta pasta).
5. **Defeito latente REAL da suíte e2e encontrado pelo teste novo e corrigido
   (a causa-raiz do flake, não um retry):** a limpeza do copiloto em
   `test_minhas_operacoes` detectava `"LIGADO" in texto` — **substring de
   "DESLIGADO"** → sempre verdadeiro → o copiloto manual nunca era desligado e
   contaminava a sessão compartilhada; o novo teste PLAY batia no guarda
   `_guarda_do_pedido` (`409 — modo Copiloto Manual ligado`). Fix: ler o texto
   EXATO do `<b>` (`to_have_text(r"^(LIGADO|DESLIGADO)$")`). E2E
   `13 passed` nas execuções seguintes, sem flake.
6. **Lint:** `ruff check tests/test_modo_simples.py tests/e2e/test_panel_e2e.py`
   → `All checks passed!` (1 correção SIM300 — condição Yoda).
7. **Sintaxe JS:** pinada pela suíte (o teste estático `node --check` roda sobre
   o `<script>` concatenado do painel — 27 testes verdes).

## Limitações declaradas (honestidade)

- **Homologação no EXE INSTALADO ainda NÃO executada para o Modo Simples** —
  o navegador real foi contra instância temporária (uvicorn + Playwright +
  chrome-devtools), não contra o binário `--windowed` recém-instalado. Isso é
  deliberadamente parte do **TD-5** (matriz de aceite dos dashboards: 12
  testes + 10 passos do iniciante no app instalado), que re-homologa também
  este wizard de ponta a ponta no exe. Nada declarado homologado no exe.
- **Nenhuma chamada de API real de corretora/IA executada** na homologação ao
  vivo (nenhuma credencial autorizada; ambiente de teste sem chaves — o
  CONECTAR foi validado por pin de contrato + rota real, não com chave real).
- REAL permanece `BLOCKED_BY_POLICY` em todas as provas; nenhum teste
  destrutivo; `%APPDATA%\DZ23-Trading-AI\` intocado; o app do dono em `:8000`
  não foi tocado (PID 13800 preservado).
- "PLAY" aqui liga o **agendador PAPER** existente — é o mesmo botão da mesa;
  não habilita nenhuma execução real.
- Screenshots são da instância temporária (componentes idênticos ao painel do
  pacote, servidos pelo MESMO `index.html` do repositório).
