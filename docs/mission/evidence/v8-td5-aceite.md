# V8 TD-5 — Matriz de aceite dashboard + UX (12 itens + 10 passos do iniciante) e PWA

- **Missão:** V8 §4 ("executar E2E no bundle Windows instalado"), §16 (matriz requisito→implementação→teste→evidência;
  eliminar falhas reproduzíveis; nada de skip para ocultar falha), §18 e V8.1 §13; tasks #94/#99/#100/#101.
- **Branch:** `fix/instalador-atualizacoes-windows` — `ac2a1bc` (matriz + 2 fixes responsivos), `63a4622`
  (job de aceite no bundle do artefato CI) e `b3e9dae` (venv do runner com extras + diagnóstico de coleta).
- **Data:** 2026-10-14.
- **Suíte:** `tests/e2e/test_aceite_td5.py` (13 testes, Playwright + navegador real). O MESMO arquivo roda em
  dois alvos: instância local (uvicorn + DATA_DIR temporário, padrão) e alvo externo via `DZ23_E2E_BASE_URL`
  (é assim que o bundle do artefato CI é exercitado).

## 1. Matriz requisito → teste → evidência → status

Nomes de arquivo abaixo são comuns aos dois alvos: corridas **locais** gravam em
`docs/mission/evidence/e2e/`; no **bundle** em `docs/mission/evidence/v8-td5-exe/` (18 capturas + placar + log).

| # | Requisito (fonte) | Teste | Evidência | Local | Bundle CI |
| :- | :--- | :--- | :--- | :--- | :--- |
| 01 | Fluxo principal CONECTAR → MODO → PLAY; REAL visível porém BLOQUEADO (§11/§18) | `…_01_inicio_assistente_conectar_modo_play` | `td5-01-inicio.png` | PASS | — |
| 02 | Painel Global do Maestro: PAPER/TESTNET/LIVE nunca somados; supervisão não-cega (§10) | `…_02_painel_global_maestro` | `td5-02-painel-global.png` | PASS | — |
| 03 | Sala do trader: gráfico profissional + portfólio do ledger; LIVE bloqueado (§9; TD-2/TD-3) | `…_03_sala_do_trader_com_grafico` | `td5-03-sala-trader.png` | PASS | — |
| 04 | Análise do copiloto com fonte/horário verificáveis; custos NUNCA estimados; viés honesto (§3) | `…_04_analise_do_copiloto` | `td5-04-analise-copiloto.png` | PASS | — |
| 05 | Botão "Acompanhar esta operação" visível/útil; prefill do registro; jamais cria/cancela ordens (§4) | `…_05_botao_acompanhar_visivel_com_prefill` | `td5-05-acompanhar-prefill.png` | PASS | — |
| 06 | Registro do que FOI executado (preço real, taxas, horário) + diário; sem preço ⇒ recusa (§3) | `…_06_registro_posicao_e_diario` | `td5-06-registro-diario.png` | PASS | — |
| 07 | Monitor emite alerta quando regra objetiva cruza (stop 159 × preço 158.5 de fixture) (§4/§5) | `…_07_monitor_alerta_de_stop_objetivo` | `td5-07-alerta-stop.png` | PASS | — |
| 08 | Centro de Alertas: canais com estado honesto, Caixa de entrada (fila durável), matriz (§5) | `…_08_centro_de_alertas` | `td5-08-centro-alertas.png` | PASS | — |
| 09 | Configuração de IA visual: ≥8 provedores, fallback, orçamento, consumo/custo (§6) | `…_09_config_ia_provedores_modelos_consumo` | `td5-09-config-ia.png` | PASS | — |
| 10 | Mesa com os 10 traders; fonte declarada; RODANDO/PARADA (§9) | `…_10_mesa_dez_traders` | `td5-10-mesa-dez-traders.png` | PASS | — |
| 11 | PWA: SW + manifest servidos; push honesto; botão Instalar não finge (PWA §2/§4/§5) | `…_11_pwa_instalacao_e_push_ui` | `td5-11-pwa-push.png` | PASS | — |
| 12 | Responsivo 390/320 px SEM rolagem horizontal no documento (PWA §3; defeitos reais → §3) | `…_12_responsivo_mobile_sem_corte` | `td5-12-manual-390.png`, `td5-12-manual-320.png` | PASS | — |
| 13 | Fluxo do iniciante em 10 passos, ponta a ponta, com persistência no servidor (§18; aceite do proprietário) | `…_13_fluxo_iniciante_dez_passos` | `td5-13-*.png` (01/03/05/07/09) | PASS | — |

**Ordem declarada entre itens (não é falha):** o item 08 cobra na caixa de entrada o alerta de stop gerado pelo
item 07 — mesmo sistema de fila durável, prova encadeada intencional (nota no docstring da suíte).

## 2. Execuções e placares

| Alvo | Comando | Placar | Quando |
| :--- | :--- | :--- | :--- |
| Local (uvicorn, DATA_DIR temporário) | `uv run --with playwright pytest tests/e2e/test_aceite_td5.py -q` | **13/13** | 2026-10-14 |
| Local — suíte e2e completa | `uv run --with playwright pytest tests/e2e -q` | **26/26**, 4 corridas seguidas (~41–42 s) | 2026-10-14 |
| Local — suíte unitária completa | `uv run pytest -q` | **3002 passed / 6 skipped / 71 subtests** (exit 0, 297,25 s — estado final do código, pós-§17/#106) | 2026-10-14 |
| Bundle do artefato CI (runner Windows virgem) | job `aceite-bundle` (ver §5) | **13/13** — 37,79 s (run `38596224361`, commit `b3e9dae`) | 2026-10-14 |
| Bundle do artefato CI — **artefato final** (RC §19 + V8.1 §4/§6) | job `aceite-bundle` (ver §5) | **13/13** — 75,63 s (run `38599535042`, commit `d131226`) | 2026-10-14 |

## 3. Defeitos REAIS encontrados pela matriz e corrigidos (RED→GREEN)

A sondagem a 320 px (walk JS de `body *` comparando `getBoundingClientRect().right` com
`document.documentElement.clientWidth`, excluindo subárvores `position:fixed`) isolou dois estouros de verdade
do app — não do teste — e o teste 12 ficou **auto-diagnóstico** (imprime os elementos culpados na falha):

1. **`#quitAppBtn` estourava a topbar em +15 px (todas as páginas, ≤760 px, modo Dono Único).**
   Causa-raiz: `boot()` des-esconde "Fechar aplicativo" quando `multi_tenant===false` (modo do bundle Windows),
   e a regra móvel escondia só `.identity-copy`. O botão (102 px) empurrava `div.identity` para fora da tela.
   Correção em `service/static/index.html`: `#quitAppBtn` entrou na regra `display:none` do breakpoint ≤760 px.
2. **Valores longos do monitor estouravam `#manualLista` em +49 px (320 px).**
   Causa-raiz: `manLines()` renderiza valores com `JSON.stringify` — `{"CONNECTION_LOST":1,"MONITORING":1}` é uma
   string sem quebra possível dentro de `<b>`. Correção: `.metric-line b{overflow-wrap:anywhere}`.

Prova: após os fixes, `scrollWidth - innerWidth ≤ 1` em 390 e 320 px nas páginas inicio/manual/mesa/config com a
mesa QUENTE (monitor já com tick), e as 4 corridas completas de e2e seguidas ficaram verdes.

## 4. Ressalvas declaradas (honestidade — nada escondido)

- **1 corrida transiente não reproduzida:** numa única passagem da suíte completa sob carga, 2 testes falharam
  por timeout de aquecimento (`test_aceite_td5_07` e `test_panel_e2e::test_modo_simples`); as 4 corridas
  seguintes ficaram 26/26. Registrada aqui — **não foi skipada nem removida**; o valor pinado do fixture não mudou.
- **Dados de fixture provam a fiação, não o mercado:** os números vivos (158.5 etc.) vêm da fonte rotulada
  "fixture"; com fonte real conectada o mesmo caminho é exercitado, mas isso é homologação de conector (G5).
- LIVE segue `BLOCKED_BY_POLICY`; nenhuma tag/Release criada.
- O bundle é exercitado **no runner do CI** (ambiente isolado, APP DATA descartável) — pela regra da missão
  (§13: "Utilizar ambientes isolados"; e o launcher REUTILIZA instância saudável da faixa 8000–8010, então a
  estação do dono, com o app dele rodando, é por definição um ambiente NÃO isolado). O harness
  `scripts/homolog_aceite_td5_bundle.py` tem guarda explícita que ABORTA se qualquer instância DZ23 já responde
  na faixa — verificado localmente: abortou sem tocar a instância do dono (porta 8000).

## 5. Aceite no bundle do artefato CI (run + evidências)

- **Run:** `38596224361` (push de `b3e9dae`, workflow `Windows Release`) — `build` SUCCESS (3m55s) e
  `aceite-bundle` SUCCESS (2m27s); `publish-release` skipped (sem tag/release — publicação aguarda
  autorização explícita do dono).
- **Alvo:** runner `windows-latest` virgem; ZIP do artefato `dz23-windows-x64` baixado, `sha256sum -c
  SHA256SUMS.txt` OK e extraído em `bundle/`; `%APPDATA%` temporário (`dz23-aceite-bundle-*`); a guarda de
  isolamento do harness confirmou que NENHUMA instância DZ23 respondia na faixa 8000–8010 antes de iniciar.
- **Resultado:** `13 passed in 37.79s` — a matriz TD-5 completa (12 itens de dashboard/UX + fluxo do
  iniciante em 10 passos) rodou com navegador real contra o bundle do artefato. Nenhum teste foi pulado
  (`skipped`); o harness exige contagem coletada == aprovada e rc=0.
- **Evidências (artefato `aceite-td5-bundle-evidencias`, cópia curada em `docs/mission/evidence/v8-td5-exe/`,
  20 arquivos):** 18 capturas (`td5-01`…`td5-13-09`), `pytest-aceite-bundle.txt` (placar nominal) e
  `launcher.log` (healthz na 1ª tentativa, fluxo de 1ª execução, `quit` gracioso com exit 0).
- **Por que o 1º run do job falhou (causa-raiz consertada em `b3e9dae`):** o venv do runner tinha só as
  dependências base (`uv run --with playwright`) — `pytest` não existia e a coleta dava `coletados=0` com o
  stderr oculto. Agora o job sincroniza `--extra desk --extra dev`, instala o Playwright no venv do projeto
  e o harness diagnostica ferramentas e coleta (imprimindo stdout+stderr) em vez de falhar mudo.
- **Re-citação do artefato final (registrada em 2026-10-14):** `38599535042` (push de `d131226` — pacote
  RC §19 + V8.1 §4/§6) — `build` SUCCESS e `aceite-bundle` SUCCESS: **13 passed in 75.63s**, mesmo desenho
  (ZIP do artefato `dz23-windows-x64`, `sha256sum -c` OK, `%APPDATA%` temporário, guarda de isolamento);
  `publish-release` skipped. O artefato `aceite-td5-bundle-evidencias` foi **baixado** e copiado para
  `docs/mission/evidence/v8-td5-exe/` (mesmos 20 arquivos, 18 capturas regeradas) — a prova no bundle agora
  corresponde ao artefato do commit da RC. O run `38596224361` permanece como prova original do harness.
