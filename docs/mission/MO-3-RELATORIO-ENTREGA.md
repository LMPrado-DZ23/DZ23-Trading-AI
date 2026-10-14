# MO-2 (implementação) + MO-3 (relatório, testes e evidências)

**Missão:** "DZ23 Trading AI — Auditoria de Microestrutura e Observabilidade" (comparação com `jarrodwatts/jev-trader`).
**Data:** 2026-10-14 · **Branch:** `fix/instalador-atualizacoes-windows` · **Base desta entrega:** `016577b` (CI verde — Release `38638400847`, Matriz `38638400668`, ambos 2026-10-14T21:12:14Z)
**Escopo:** APENAS as lacunas comprovadas na matriz MO-1 (`docs/mission/MO-1-MATRIZ-AUDITORIA.md` §5): MO-2-A, MO-2-B e MO-2-C.
**Regra desta entrega:** nada declarado sem evidência; o que não foi exercitado está declarado como tal.

---

## 1. O que foi implementado (task #120; #122/#123/#124)

### MO-2-A — Adaptador read-only de microestrutura L2 (fecha 1.2–1.6, amplia 1.7/1.8)

| Arquivo | Papel |
| :--- | :--- |
| `tradingagents/desk/microestrutura.py` (novo, PURO) | `LeituraDeLivro` + `analisar_livro()` + `slipagem_estimada()`; estados honestos `disponivel / indisponivel / inconsistente / vencida / nao_suportado / nao_aplicavel`; sem rede, sem relógio interno, sem caminho de ordem |
| `tradingagents/desk/market.py` | `PublicMarketFeed.leitura_de_livro()` — leitura pública via `fetch_order_book`; gate de capacidade (`has["fetchOrderBook"] is not True` ⇒ `nao_suportado`, **sem rede**); cache curto (`book_cache_seconds=10`); falha de rede com leitura anterior ⇒ `vencida` (nunca número inventado) |
| `tradingagents/desk/sala_trader.py` | `retrato_do_trader(..., livro=...)` — repassa a leitura ao payload da sala |
| `service/app.py` | `_retrato_da_sala` fia a leitura real; em fixture ⇒ `nao_aplicavel` honesto |
| `service/static/index.html` | Bloco "Livro (L2)" na sala (bid/ask/spread bps/profundidade/imbalance/slippage por tamanho) com o estado declarado quando não há dado |

**Testes:** `tests/test_microestrutura.py` — **22 testes** (válido com métricas exatas · imbalance assimétrico por lado · ausente/inconsistente: lado vazio, nível ≤ 0, livro cruzado · vencida mantém a última leitura · slippage por livro; lado de venda nos bids; profundidade insuficiente ⇒ `None`; corretora sem nativo ⇒ `nao_suportado` sem rede; emulado não vale como nativo; cache curto; falha de rede; **garantia estrutural read-only** — o módulo não conhece `create_order`/`ccxt`; sala renderiza L2; rota em fixture declara `nao_aplicavel`).

**Validade temporal:** cada leitura carrega o instante de obtenção; idade > `max_idade_s` ⇒ estado `vencida` com o motivo — o consumidor nunca recebe número vencido como se fosse vivo.

### MO-2-B — Trilha da decisão na sala (fecha 2.2/2.5; consolida 2.1/2.3/2.4)

| Arquivo | Mudança |
| :--- | :--- |
| `tradingagents/desk/sala_trader.py` | `marcacoes_de_execucao()` passa a devolver **taxa real por execução** a partir das entries `FEES:` do livro-razão: conta única ⇒ soma com moeda declarada; moedas diferentes ⇒ `None` + "soma recusada"; sem lançamento ⇒ `None` + "sem lançamento de taxa" |
| `service/static/index.html` | Bloco "Trilha da decisão" na sala com 5 passos: **1 Dados de entrada** (mercado/cutoff/qualidade/rótulo da fonte) → **2 Motivo de operar ou esperar** (última marcação com taxas) → **3 Maestro** → **4 Risco** → **5 Custos** (custos acumulados do diário + taxa da última execução); chips de marcação mostram a taxa por execução |

**Sem sistema paralelo:** o passo 3 consome a rota JÁ EXISTENTE `/v1/maestro/painel` (função `salaTrilhaMaestro`), preenchendo `#salaTrilhaMaestro` de forma assíncrona; sem dado ⇒ falta declarada ("nada foi inventado"), indisponibilidade da rota ⇒ declarada com o status.

**Testes:** `tests/test_sala_trilha.py` — **8 testes** (taxa real de compra/venda; ausência honesta; recusa de soma entre moedas; bloco no DOM; uso da rota existente e nenhuma rota nova; cobertura dos 5 passos; taxa exibida nas marcações). e2e: `tests/e2e/test_aceite_td5.py` item 03 estendido (trilha + elo do Maestro vindo da rota real, aguardando o preenchimento assíncrono).

### MO-2-C — Latência por estágio e trilha por ordem (fecha 3.5/3.6/3.7)

| Arquivo | Mudança |
| :--- | :--- |
| `tradingagents/desk/execution.py` | `OrderRecord` ganha `criada_em/enviada_em/reconhecida_em/primeiro_fill_em/terminal_em/atualizada_em` (ISO-8601 UTC; `""` = desconhecido); `carimbar_criacao()` só na criação de ordem NOVA; `carimbar_transicao(novo)` em TODAS as transições de estado; `latencias_ms(rec)` deriva `criacao_ate_envio / envio_ate_aceite / aceite_ate_execucao / envio_ate_execucao` (`None` quando falta carimbo — nunca zero)
| persistência | `_ordem_para_doc`/`_ordem_de_doc`: esquema continua `dz23.oms/2` (chaves aditivas); documento antigo SEM carimbos restaura com `""` — **sem carimbo retroativo** |
| `service/app.py` | Sala (`_retrato_da_sala`) e Mesa (`/v1/desk/status.orders`) expõem os 6 carimbos + `latencias_ms` |
| `service/static/index.html` | Helper global `omsLatTxt(o)`; coluna "Trilha (UTC)" na tabela "Ordens" da Mesa (com `title` declarando "carimbos UTC do OMS — vazio quando desconhecido"); a sala usa o mesmo helper nas ordens abertas |

**Testes:** `tests/test_oms_tempos.py` — **11 testes** (ordem nova nasce carimbada e ordenada · cancelamento carimba transições e terminal · latências por estágio · sem carimbo ⇒ todas `None` · **ACK sem preenchimento ⇒ latências de execução `None` (honesto)** · roundtrip de persistência preserva · documento antigo restaura sem inventar · rota da Mesa expõe carimbos/latências **reais** após ciclo (`POST /v1/desk/cycle`) · sala na rota · coluna no painel).

---

## 2. Prova por exigência da missão

1. **Microestrutura (missão §1):** "adaptadores de leitura por corretora homologada, com validade temporal e testes de dados ausentes ou inconsistentes" — `leitura_de_livro()` só usa o nativo do ccxt (capacidade declarada em `personal_exchange_capabilities.py`), carimba a obtenção, degrada para `indisponivel`/`vencida`/`inconsistente`/`nao_suportado`; os 4 cenários têm teste dedicado. Nada que já existia foi recriado (MO-1 §1).
2. **Transparência (missão §2):** "trilha completa da decisão, incluindo dados utilizados, motivo de operar ou esperar, resultado do Maestro, risco e custos" — os 5 passos na sala usam o payload da própria sala + a rota existente do Maestro; as taxas vêm do livro-razão (dado que já existia).
3. **Observabilidade financeira (missão §3):** "separação entre intenção, ordem enviada, ordem aceita, execução confirmada, posição e resultado" — carimbos distintos por estágio no OMS, expostos na Mesa e na sala; reconciliação/deduplicação/estado-desconhecido permanecem como em MO-1 §3 (EXISTE, com citação de testes), sem alteração.
4. **Não adotado por decisão (MO-1 §4):** motor de 300 ms e Monad/Kuru — NÃO NECESSÁRIO, com justificativa registrada.

---

## 3. Evidências de teste (comandos e resultados)

| Prova | Comando (resumo) | Resultado |
| :--- | :--- | :--- |
| Testes novos MO-2 | `.venv/Scripts/python.exe -m pytest tests/test_microestrutura.py tests/test_sala_trilha.py tests/test_oms_tempos.py -q` | **22 + 8 + 11 = 41 verdes** |
| Conjunto focado (OMS, sala, livro vivo, painel estático, decisão, alertas, inteligência, selo LIVE, gates FIN) | 10 arquivos | **212 passed** |
| Regressão ampla de execução/OMS (39 arquivos) | lote de `test_desk_*`/`test_oms_*`/persistência/crash | **710 passed** |
| Validação estática do painel (incl. `node --check` do JS) | `tests/test_ia_painel_static.py` | **27 passed** |
| e2e da sala (servidor real + navegador real, item 03 + trilha) | `uv run --with playwright pytest tests/e2e/test_aceite_td5.py -k "03" -q` | **1 passed** (3,71 s) |
| Suíte completa (sem e2e) | `.venv/Scripts/python.exe -m pytest tests -q --ignore=tests/e2e -p no:cacheprovider` | **`3079 passed / 4 skipped / 71 subtests`** (exit 0, 357,71 s; 4 skips ambientais declarados: `langchain_aws`/bedrock, permissões POSIX no NTFS, postgres sem `DATABASE_URL` — roda no CI, `lightgbm`; baseline não-e2e 3042 coletados + 41 novos = 3083 coletados = 3079+4 ✓) |

**Prova de mudança real (RED na base):** na base `016577b`, `service/app.py` tinha **0** ocorrências de `criada_em` e o painel **0** de "Trilha (UTC)"; na árvore desta entrega, **2** e **1** respectivamente — os testes novos de rota/DOM falhariam na base (KeyError em `o["criada_em"]` no teste de runtime).

**CI desta entrega:** push pendente — resultados citados em `TRADING-MISSION-STATE.md` **após verdes** (regra vigente; o aceite-bundle no bundle Windows re-executa a matriz TD-5 incl. o item 03 estendido).

---

## 4. Garantias preservadas (declaração vinculante)

- **Não mudou:** os 10 traders, o Maestro, o ledger (partidas dobradas/multimoeda), o PAPER determinístico, o agendador, os gates financeiros, a trava `MESA_LIVE_LIBERADA` e o fail-closed do LIVE (`BLOCKED_BY_POLICY`).
- **Nada escreve em corretora:** MO-2-A é leitura pública de mercado (sem credencial); nenhuma ordem real; nenhuma ativação de LIVE.
- Esquema persistido do OMS inalterado (`dz23.oms/2`; chaves aditivas, ausência tolerada).
- Nenhum segredo/chave usado, ecoado ou persistido; a instalação do dono (`:8000`) e o `%APPDATA%` não foram tocados.

---

## 5. Ressalvas e limitações declaradas (nada oculto)

1. **Livro L2 ao vivo depende de rede pública da estação** — o caminho real (feed real + corretora com nativo) tem cobertura de unidade/HTTP com trocas falsas; a prova contra corretora real de mercado não foi executada nesta rodada (offline/limite declarado). O estado `indisponivel`/`vencida` honesto está testado.
2. **Superfícies com níveis de prova diferentes:** sala = e2e em navegador real + DOM estático + rota; Mesa ("Trilha (UTC)") = rota em runtime HTTP + DOM estático — sem e2e de navegador dedicado nesta rodada (o aceite TD-5 no CI do push roda a matriz completa no bundle, mas o item da Mesa não foi estendido).
3. **Documentos antigos do OMS** restauram sem carimbo (`""`) por desenho — a trilha temporal deles fica declarada desconhecida (nenhum carimbo retroativo).
4. **e2e completo local** não foi re-executado nesta rodada; rodou o item 03 (o que mudou na sala). O aceite integral é re-provado no CI do push (aceite-bundle).
5. **Nada de LIVE/tag/release** — inalterado; publicação depende de autorização explícita do dono.
