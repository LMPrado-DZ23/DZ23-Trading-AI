# V8 — TD-1: Painel Global do Maestro (consolidação multi-conta) — evidências

Requisito do complemento V8 (verbatim): *"Não misturar resultados PAPER, TESTNET
e LIVE. Não somar patrimônios ou resultados de moedas diferentes sem conversão
identificada e verificável."* — o Painel Global consolida os 10 traders e as
salas da Escola Central **sem operar, sem certificar e sem autorizar** nada;
LIVE permanece bloqueado.

Data das provas: 2026-10-14. Branch `fix/instalador-atualizacoes-windows`.
Arquivos: `tradingagents/desk/painel_global.py` (novo, módulo puro),
`service/app.py` (rota `GET /v1/maestro/painel`), `service/static/index.html`
(card `#painelGlobal` na página Mesa de Agentes),
`tests/test_painel_global.py` (novo, 20 testes).

## Requisito → implementação → evidência

| Requisito (complemento V8) | Implementação | Evidência |
|---|---|---|
| PAPER/TESTNET/LIVE nunca somados | `retrato_global()` omite o total quando há mais de um modo presente; cada modo ganha subtotais próprios por moeda; LIVE sempre `{"bloqueado": true}` com o motivo da autorização do proprietário | unidade `TestSegregacaoModosEMoedas`; prova viva: `patrimonio.total = null` + `motivo_sem_total = "modos diferentes nunca compartilham um total (PAPER/TESTNET/LIVE têm contas e riscos próprios)"` (JSON anexo abaixo); captura `painel-01-global.png` |
| Moedas diferentes nunca somadas sem conversão | Subtotal por `(modo, moeda)`; total só existe quando **todos** os livros têm a mesma moeda declarada E o mesmo modo da mesa; moeda não declarada ⇒ total omitido | unidades `test_moedas_diferentes_por_moeda…`, `test_livro_sem_moeda_declarada_omite_total`; render vivo: subtotais `PAPER·USDT` e `TESTNET·USDT` + linha "Total omitido" |
| "conversão identificada e verificável" | Somente soma de valores já na mesma moeda; nunca estima câmbio; texto da regra impresso no próprio painel | constante `_REGRA_MOEDAS` no módulo + rodapé do card ("moedas diferentes não entram na mesma conta sem conversão identificada e verificável") |
| Números só de ledger/eventos verificáveis | O painel **lê** `/v1/desk/status`, livro do ledger, contabilidade da mesa, salas da Escola e relatório do Maestro; nenhum número inventado; nenhuma rota de escrita | teste `TestModuloPuro` (fonte do módulo não contém `create_order`, `ccxt`, `import requests` — é puro, sem I/O) |
| Patrimônio por conta/mercado | Tabela "Conta/sala · Modo · Moeda · Saldo realizado · Taxas · Posições" (salas da Escola) + linha da Mesa com saldos da contabilidade (`balanceada`/`DESBALANCEADA`) | captura viva: `binance|pa-td1|spot|paper` e `binance|tb-td1|spot|testnet` lado a lado, nunca somadas |
| Exposição e risco agregado | Exposição por modo→moeda (qtd × preço de entrada, mesma fórmula do Maestro) + figura do Maestro rotulada como **não-conversão** ("leitura de supervisão, não conversão") | unidade `test_exposicao_paper_por_moeda`; JSON vivo: `exposicao.maestro.exposicao.por_sala` com as duas salas |
| 10 traders e estados | `traders`: total, por tier, por estado, suspensos, lista com evidência e último motivo de não operar | prova viva: `10 · homologados 3 · experimentais 7`; canvas de "oportunidades" com vetos/abstenções do último ciclo |
| Oportunidades do último ciclo | Vetos, abstenções e descartados do último ciclo com o motivo do próprio trader por último (voz dele vence) | unidade `test_oportunidades_por_trader…`; pin da ordem vetos→abstenções no módulo |
| Comparações honestas | Comparação entre traders usa **SOMENTE** evidência madura; sem maduros ⇒ média omitida com motivo (nunca estimada) | unidade `TestOportunidadesEComparacoes`; render vivo: "sem evidência madura neste tier — média não estimada" |
| Alertas ativos | Alertas reais do estado da mesa (mesmo `_estado_para_alertas` do painel existente) | render vivo: `mesa_parada` com ação ("Ligue novamente na aba Mesa de Agentes…") |
| Supervisor não cega quando a mesa não sobe | Rota captura a falha da mesa (`mesa_motivo`) e ainda entrega salas/traders/escola; modalidade "supervisão" | unidade `TestRotaHttp::test_mesa_indisponivel_painel_ainda_responde` |
| LIVE bloqueado até homologação + autorização do proprietário | `modos.live = {"bloqueado": True, "motivo": "…autorização explícita do proprietário"}` + aviso do card | unidade `test_live_sempre_bloqueado_e_rotulado`; render vivo: badge "LIVE BLOQUEADO — negociação real permanece bloqueada…" |
| Não declarar conclusão só porque o gráfico aparece | Provas = unidade + HTTP + DOM + instância viva (JSON + capturas); a homologação Windows segue pendente e declarada como tal | este documento + suíte completa verde |

## Arquitetura (isolamento e clareza)

- Módulo puro `tradingagents/desk/painel_global.py`: `retrato_global(...)` não
  faz I/O e não importa rede/broker — recebe dados já coletados e devolve o
  retrato. Fácil de testar por unidade (14 testes) e impossível de virar
  "segundo motor" de decisão.
- Rota `GET /v1/maestro/painel` (só autenticação, como as demais leituras):
  coleta mesa + Escola Central + Maestro e injeta tudo no módulo puro. Quando a
  mesa não está de pé, o painel continua respondendo em modo supervisão com
  `mesa_motivo`.
- Descoberta relevante: o ledger da mesa **nunca** é transplantado para a sala
  espelho (boot em `service/app.py`), então "patrimônio da mesa + saldos
  realizados das salas" só é somado quando modo e moeda são únicos e
  declarados; fora disso o total é **omitido com motivo**.

## Provas frescas (comandos e resultados exatos)

1. **RED:** `tests/test_painel_global.py` escrito primeiro; primeira execução
   falhou (módulo `tradingagents.desk.painel_global` ainda não existia) e a
   rota na instância viva respondia **404** — comportamento esperado antes da
   implementação.
2. **GREEN focado:** `pytest tests/test_painel_global.py -q` →
   **20 passed** (14 unidade + 3 rota HTTP + 3 DOM/estáticos).
3. **Regressão focada:** `pytest tests/test_painel_global.py
   tests/test_modo_simples.py tests/test_service_escola.py
   tests/test_ia_painel_static.py -q` → **87 passed**.
4. **Lint:** `ruff check` → **All checks passed!** (após C416/C408 corrigidos).
5. **Suíte completa:** `pytest -q` →
   **2891 passed, 5 skipped, 71 subtests passed** (exit 0; 2871 antes do TD-1 +
   20 novos; duas execuções independentes com o mesmo resultado).
6. **E2E navegador real (Playwright + uvicorn real):**
   `uv run --with playwright --python .venv/Scripts/python.exe python -m pytest
   tests/e2e -q` → **13 passed in 31.26s** (exit 0; mesma suíte de 13 testes de
   SS-5, página Mesa incluída — a suíte principal roda sem Playwright e o marca
   `skipped`, contado nos 5 skips acima).

## Homologação AO VIVO em instância temporária isolada (porta 8786)

Ambiente: `DATA_DIR` em temp, `TRADINGAGENTS_BROKER_MODE=paper`, **sem** chaves
de IA reais (`env -u GROQ_API_KEY -u MISTRAL_API_KEY`) — o app do dono em
`:8000` intocado, nenhuma ordem, nenhum kill.

Prova de segregação (JSON de `/v1/maestro/painel`, recorte verbatim):

```json
"modos": {"mesa": "paper", "presentes": ["paper", "testnet"],
          "regra": "PAPER, TESTNET e LIVE nunca compartilham um mesmo número — cada modo tem subtotais próprios",
          "live": {"bloqueado": true,
                   "motivo": "negociação real permanece bloqueada até homologação e autorização explícita do proprietário"}},
"patrimonio": {"total": null,
               "motivo_sem_total": "modos diferentes nunca compartilham um total (PAPER/TESTNET/LIVE têm contas e riscos próprios)",
               "por_modo": {"paper": …, "testnet": …}},
"traders": {"total": 10, "por_tier": {"homologado": 3, "experimental": 7}},
"comparacoes": {"por_tier": {"homologado": {"maduros": 0, "media_taxa_acerto": null,
                 "motivo": "sem evidência madura neste tier — média não estimada"}, …}},
"aviso": "leitura consolidada — não opera, não certifica e não autoriza operação real; LIVE permanece bloqueado"
```

Render AO VIVO no navegador (login real → `showPage('mesa')`):

- Badge: "LIVE BLOQUEADO — negociação real permanece bloqueada até homologação
  e autorização explícita do proprietário".
- "Traders 10 · homologados 3 · experimentais 7".
- "Mesa (USDT) 1000 · balanceada" + subtotais "PAPER · USDT" e "TESTNET · USDT".
- "Total: omitido — modos diferentes nunca compartilham um total
  (PAPER/TESTNET/LIVE têm contas e riscos próprios)".
- Tabela de contas com `binance|pa-td1|spot|paper` e `binance|tb-td1|spot|testnet`
  lado a lado.
- Comparações: "sem evidência madura neste tier — média não estimada".
- Fonte: "fixture — dados de FIXTURE — provam a mesa, não o mercado".

Capturas nesta pasta: `painel-01-global.png` (card completo) e
`painel-02-mesa-contexto.png` (página Mesa com o card no contexto). JSON bruto
salvo em `C:/Users/DZ23/AppData/Local/Temp/dz23-td1-painel.json` (fora do
repositório, como artefato de prova).

## Limitações declaradas (honestidade de escopo)

- Os dados vivos desta homologação são de **fixture** (mesa parada, saldo 1000
  simulado, sem posições) — provam a fiação e a segregação, **não** o mercado.
- A soma "mesa + salas" no subtotal único foi provada por unidade e por HTTP
  (`test_mesmo_modo_e_moeda_soma_rotulada`); na instância viva havia dois modos
  presentes, então o total permaneceu corretamente omitido.
- Compatibilidade/instalação Windows (homologação no app instalado, 10 passos
  do usuário iniciante) segue **PENDENTE** — pertence ao TD-5 e continuará
  declarada como pendente até prova reproduzível.
- Nenhuma tag/Release criada; LIVE continua bloqueado por código.
