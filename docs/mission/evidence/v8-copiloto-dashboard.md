# V8 — TD-4: Dashboard do Copiloto Manual (sinais determinísticos + botão "Acompanhar esta operação") — evidências

Requisitos do complemento V8 (TD-4, §3): *"escolher corretora e ativo → o
sistema analisa com dados identificados → apresenta oportunidade, riscos,
custos, horizonte e condições de invalidação → o usuário executa
manualmente → registra preço efetivo, quantidade, taxas e horário → clica
em 'Acompanhar esta operação' → o sistema monitora → atualiza/recalcula →
alertas objetivos → o usuário registra saída parcial/total → o sistema
registra o resultado e alimenta diário + Escola"*. Não-negociável: *"O
Copiloto Manual jamais poderá criar ou cancelar ordens na corretora"*.
§4: *"BOTÃO ACOMPANHAR ESTA OPERAÇÃO — O botão precisa estar visível e
funcional nos locais apropriados"*. E: *"Não prometer previsão exata de
preços nem lucros"*; *"os gráficos financeiros devem derivar de ledger e
eventos verificáveis"*; *"não misturar PAPER, TESTNET e LIVE"*; nunca
números simulados apresentados como reais.

Data das provas: 2026-10-14. Branch `fix/instalador-atualizacoes-windows`
(HEAD `b4a6f61` + este trabalho). Arquivos: `tradingagents/desk/
copiloto_sinais.py` (novo, módulo PURO), `service/app.py` (rota `GET
/v1/manual/sinais`), `service/static/index.html` (card "Análise do
copiloto" + prefill + renome do botão de registro + campo de custos),
`tests/test_copiloto_sinais.py` (novo, 31 testes), `tests/e2e/
test_panel_e2e.py` (seletor do botão atualizado para o nome novo).

O fluxo de 11 passos já existia em grande parte desde o E3/E4 (registro →
monitor → alertas → diário → Escola); o que o TD-4 fecha é o **elo que
faltava**: a análise determinística ANTES da execução manual (§3 passos
1–3) e o botão com o nome exato do §4.

## Requisito → implementação → evidência

| Requisito (TD-4) | Implementação | Evidência |
|---|---|---|
| Escolher ativo + tempo gráfico e o sistema analisar (passos 1–2) | Card "Análise do copiloto" em `#page-manual`: input `#sigInstr` + select `#sigTf` (1m…1d) + botão "Analisar mercado"; rota `GET /v1/manual/sinais?instrumento=&timeframe=` | unidades `TestRotaSinaisHttp` (4) + `TestSinaisNoDOM` (5) — card, ids e rota pinados por substring/DOM |
| Apresentar oportunidade, riscos, custos, horizonte e invalidação (passo 3) | `analisar_mercado()` monta o retrato completo: `oportunidade` (viés ou motivo honesto), `riscos` (rotulados), `custos` (nota "NUNCA estima"), `horizonte` (por timeframe), `invalidacao` (pivô mais próximo) | unidades `TestAnaliseUnidade` (15); JSON vivo abaixo com todos os campos |
| Dados identificados — nunca número inventado (não-negociável) | Se o ativo não está na fonte, responde com `motivo` e motivo de amostra insuficiente ("ainda não há fechamentos suficientes…"); série de fixture vem rotulada `fonte:"fixture"` com aviso "os números provam a mesa, não o mercado vivo" | testes de série curta/vazia (motivo "21"/"15", "sem pivôs"); homologação viva: mesa desligada ⇒ "0 de 21"; fixture ⇒ rótulo em `fonte`/`avisos`/`riscos` |
| RSI ≥ 70 / ≤ 30 bloqueia o viés — "comprar preço esticado não é oportunidade; aguarde alívio" | `_oportunidade()` zera o viés e devolve o motivo exato; teste com série monotônica (RSI 100) | `test_rsi_sobrecomprado_bloqueia_vies_com_motivo` + espelho vendido (RSI 34.96 / 24 crescente); JSON vivo: RSI 100 ⇒ `vies:null` |
| Condições de invalidação objetivas | `_invalidacao()`: compra ⇒ maior suporte < preço (109.27 na série pinada); venda ⇒ menor resistência > preço (90.73); sem pivôs ⇒ motivo | `test_invalidacao_compra_usa_suporte_mais_proximo` / `test_invalidacao_venda_usa_resistencia_mais_proxima` / `test_sem_pivos_invalidacao_com_motivo` |
| O usuário executa manualmente e registra preço efetivo, quantidade, taxas e horário (passos 4–5) | Card "Registrar posição já executada" ganhou `#manCustos` ("taxas reais cobradas pela corretora") — enviado em `custos_entrada` no `manualCriar()` | `TestSinaisNoDOM`: `#manCustos` + slice de `manualCriar` contém `custos_entrada:$('manCustos')` |
| BOTÃO "ACOMPANHAR ESTA OPERAÇÃO" visível e funcional (§4) | O botão do card de registro foi renomeado de "Registrar posição" para **"Acompanhar esta operação"** (`onclick="manualCriar()"`), com legenda "ele nunca cria ou cancela ordens na corretora" | `TestSinaisNoDOM`: regex exata `onclick="manualCriar\(\)">Acompanhar esta operação</button>`; e2e `test_minhas_operacoes_copiloto_manual_ponta_a_ponta` atualizado e verde (13/13); captura `td4-02-acompanhar.png` |
| "Usar esta análise no registro" — a análise vira a tese do registro | `manualUsarAnalise()` preenche `#manInstr`/`#manDir`/`#manTese`/`#manInvalidacao` e rola até o card de registro | `TestSinaisNoDOM` pinando `$('manInstr')`/`$('manDir')`/`$('manInvalidação')`/`$('manTese')`/`scrollIntoView`; browser vivo: `PREFILL {'instr': 'BTC/USDT', 'tese': 'tendência de alta, mas RSI sobrecomprado (100.0)…', 'botao': True}` + captura `td4-02` |
| Monitorar → recalcular → alertas objetivos → saída parcial/total → diário + Escola (passos 6–11) | Já implementado no E3/E4 (`manual_monitor`, `/v1/manual/posicoes/{id}/eventos`, diário, fiação com a Escola) — TD-4 não trouxe caminho paralelo | e2e ponta-a-ponta cobre: registro → ligar copiloto → detalhes → execução parcial com preço obrigatório → diário; suíte E3/E4 segue verde |
| "O Copiloto Manual jamais poderá criar ou cancelar ordens" | Módulo novo é PURO: nenhum `create_order`/`cancel_order`/`ccxt`/`requests`/`execution`/`subprocess`/`socket`; a rota só autentica, lê feed e calcula; a rota NÃO boota a mesa | `TestModuloPuro` (grep pinado + AST pin da função `manual_sinais` contra `{create_order, cancel_order, close_position, executar_ordem, enviar_ordem, submit}`); `test_rota_nao_boota_a_mesa` |
| "Não prometer previsão exata de preços nem lucros" | 3 avisos fixos em toda resposta: "não é promessa de lucro nem previsão exata de preços" / "não envia ordens… quem executa é você" / "cotação de tela pode estar defasada — o preço que vale é o efetivamente executado por você"; texto no card e na página ("quem opera é você") | `test_avisos_fixos_em_toda_resposta` + `test_promessas_no_painel` + JSON vivo com os 3 avisos |
| Custos nunca estimados | `custos.nota`: "o copiloto NUNCA estima custos por você; registre as taxas reais junto da execução" | `test_custos_nunca_estima` + `#manCustos` no DOM |
| PAPER/TESTNET/LIVE não misturados | Análise usa SOMENTE a fonte de mercado da instância (fixture ou real, rotulada); nenhum resultado de ledger entra na análise | teste `test_fonte_real_nao_tem_risco_de_fixture` + `test_fixture_risco_rotulado`; JSON vivo `fonte:"fixture"` + risco rotulado |

## Homologação viva (2026-10-14)

Instância temporária em `127.0.0.1:8787` (dados isolados, nunca a porta 8000
do dono), auth por bearer:

1. **Mesa desligada (honestidade primeiro)** — `GET /v1/manual/sinais?
   instrumento=BTC/USDT&timeframe=1h` sem `_DESKS` responde o motivo "0 de
   21: a mesa ainda não tem leituras — …" com `fonte:"fixture"` — nunca
   inventa série. (rota não bootou a mesa; pinado em teste)
2. **Análise completa** — mesma rota com a mesa ativa devolve o JSON abaixo
   (salvo em `C:/Users/DZ23/AppData/Local/Temp/dz23-td4-homolog/
   sinais-btcusdt.json`):

```json
{"instrumento":"BTC/USDT","timeframe":"1h","horizonte":"1h — horizonte intradiário: cada vela vale 1 hora; a leitura vale para o dia","fonte":"fixture","qualidade":"ok","amostra":40,"preco_atual":158.5,"tendencia":{"estado":"alta","sma_rapida":152.5,"sma_lenta":143.5},"rsi":{"valor":100.0,"estado":"sobrecomprado"},"niveis":{"suportes":[],"resistencias":[]},"oportunidade":{"vies":null,"motivo":"tendência de alta, mas RSI sobrecomprado (100.0) — comprar preço esticado não é oportunidade; aguarde alívio"},"riscos":["fonte rotulada 'fixture': os números provam a fiação, não o mercado vivo","volatilidade da amostra: 45.26% …","RSI sobrecomprado (100.0) — risco de movimento contrário","risco por operação ainda não definido — declare stop e invalidação no registro"],"custos":{"nota":"… NUNCA estima custos por você …"},"invalidacao":{"motivo":"sem direção definida pela leitura — a invalidação fica com a sua tese"},"avisos":[3 avisos fixos]}
```

3. **Navegador real (Playwright)** — login → Minhas Operações → BTC/USDT/1h
   → "Analisar mercado": card renderiza tendência, RSI, níveis (—), preço
   de referência 158.5, horizonte, invalidação, custos e riscos rotulados
   (`td4-01-analise.png`); "Usar esta análise no registro" preenche a tese
   com o motivo do viés nulo e rola até o botão **"Acompanhar esta
   operação"** visível com a legenda de que o copiloto nunca cria/cancela
   ordens (`td4-02-acompanhar.png`); toast: "Análise levada ao registro —
   confira o preço REAL que você executou; cotação de tela nunca
   substitui."

## Verificação (comandos e resultados)

- `pytest tests/test_copiloto_sinais.py -q` ⇒ **31 passed** (unidade +
  HTTP + DOM; RED→GREEN registrado: coleção falhou sem o módulo; AST pin
  falhou sem a rota).
- Regressão focada (sinais + copiloto_manual + api + ui + td3_graficos +
  sala_trader + painel_global + ia_painel_static) ⇒ **195 passed**.
- E2E Playwright com servidor real (uv-injected) ⇒ **13 passed** em
  ~31 s — inclui o fluxo ponta-a-ponta do copiloto manual com o botão
  renomeado (o seletor antigo "Registrar posição" foi o único pintado
  contra o rename e foi atualizado no mesmo commit).
- Suíte completa (changeset congelado) ⇒ **2978 passed / 5 skipped / 71
  subtests**, exit 0, 255,03 s (2978 = 2947 do TD-3 + 31 novos).
- `ruff check` nos 4 arquivos tocados ⇒ **All checks passed**.

## Limitações declaradas (honestidade)

1. **Homologação no exe Windows instalado**: pendente — pertence à matriz
   do TD-5 (mesmo status de SS-5/TD-1/TD-2/TD-3). As provas aqui são em
   navegador real sobre a instância local; o pacote empacotado não foi
   reinstalado (proibido desinstalar a instalação do dono).
2. **Análise usa a fonte da instância**: com fixture, o rótulo é explícito
   ("provam a mesa, não o mercado"); com fonte real, o feed público é
   usado — nenhum número é cruzado entre fontes.
3. **+31 testes novos** sobre o TD-3; nenhum comportamento anterior
   alterado além do nome do botão (seletor do e2e atualizado no mesmo
   commit).
