# E6 — Fundação Multimercado (task #61) — evidências

Registro de classes de ativo + sessões fail-closed + avaliação escrita de
IBKR/Alpaca/B3 — **sem ativar nenhum adaptador de execução novo**. LIVE
continua `BLOCKED_BY_POLICY`.

## Entregue

1. **`tradingagents/desk/mercados.py` (novo) — camada de POLÍTICA:**
   - 7 classes do escopo: `cripto_spot` e `cripto_futuros` (homologadas —
     já têm adaptador e mesa de custos), `b3_acoes`, `b3_fiis`, `b3_etfs`,
     `us_acoes`, `us_etfs` (`execucao_homologada=False` — nenhum adaptador).
   - Metadados por classe: moeda de cotação, lote mínimo/passo, tick,
     liquidação (D+0 / D+2 / T+1), taxas por lado + spread de referência,
     sessão. `custo_ida_e_volta` documentado como ESTIMATIVA DE POLÍTICA
     (não substitui `costs.py` na contabilização).
   - `SessaoDeMercado.aberta_em()` fail-closed: fim de semana e feriado
     fecham com motivo; fora da janela fecha; classe desconhecida em
     `mercado_aberto()` ⇒ FECHADO. Feriados = snapshot anual explícito
     (`FERIADOS_2026_B3` / `FERIADOS_2026_US`), não serviço de calendário.
   - Horário de verão por REGRA, sem tzdata: EUA 2º domingo de março
     (07:00 UTC) → 1º domingo de novembro (06:00 UTC); Brasil UTC-3 fixo
     (DST extinto em 2019).
   - `registrar_classe`/`classe_de` com validação (mercado conhecido,
     lote/tick/taxas positivos, janela completa e sem cruzar meia-noite);
     `lote_valido`, `custo_da_classe`, `mercado_aberto`.
2. **`tradingagents/desk/salas.py` — classe como dimensão nova:**
   - `ContextoDaSala.classe` opcional; chave legada de 4 partes PRESERVADA
     (`corretora|conta|mercado|modo`); 5ª parte (`|classe`) só quando
     declarada. `como_dict`/`de_dict` ida-e-volta.
   - `_validar`: classe desconhecida, incoerente com o mercado ou SEM
     `execucao_homologada` ⇒ sala recusada (política não promete execução).
   - `RegistroDeSalas.migrar_sala()`: migração EXPLÍCITA, exige posições
     fechadas ("uma posição viva tem um dono só"), copia saldo, taxas,
     desfechos, priors e livro-razão (`export_transactions` →
     `from_transactions`), marca `migrada_para` no doc legado; a chave
     antiga passa a REDIRECIONAR para a sala viva (nada de estado
     duplicado); repetição/migração de classe sem homologação recusadas.
   - `Sala.pode_operar()`: janela por classe (legado sem classe mantém o
     comportamento atual — cripto 24/7 não muda); `resumo()["classe"]`.
   - `MERCADOS` estendido para `spot, futuros, opcoes_binarias, acoes,
     etfs, fiis` — agora vive em `mercados.py` e é reexportado.
3. **`service/app.py`:** `_contexto_da_sala` aceita `classe` opcional no
   corpo (retrocompatível; sem ela o comportamento é idêntico ao anterior).
4. **`tests/test_multimercado.py` (novo) — 15 testes:** registro das 7
   classes + metadados; duplicata exige `substituir`; metadata inválida
   recusada; desconhecida fecha mercado aberto; cripto 24/7 em fim de
   semana; B3 fim de semana/feriado (2026-12-25)/fora da janela; US
   EST vs EDT nas duas fronteiras de 2026 + feriado MLK; lote por classe;
   custo = 2×taxas+spread (cripto_spot 0,0022); chave legada vs estendida;
   sala com classe homologada (24/7); recusa de classe incoerente/
   desconhecida; recusa de classe não homologada MESMO com adaptador
   (`b3_teste` registrado no teste); migração copia prova viva + legado
   redireciona + reload do armazém; recusa de repetição/posição aberta/
   classe inválida/origem inexistente.
5. **`tests/test_salas.py`:** exemplo de "mercado desconhecido" atualizado
   (`("acoes",)` virou mercado legítimo da política — a intenção do teste
   "mercado desconhecido recusado" foi preservada com `("cripto_magica",)`).
6. **Avaliação escrita:** ADR-005 em `docs/mission/TRADING-DECISIONS.md`
   (decisão de política-antes-de-execução + avaliação IBKR/Alpaca/B3 com
   fontes: PUMA/EntryPoint/certificação B3, TWS API IBKR, Alpaca, Onixs,
   IMRYN, Broker Insight). Nenhum adaptador ativado; ordem sugerida de
   homologação futura: Alpaca paper (US) → corretora B3 → IBKR.

## Comandos e resultados (reproduzíveis)

- Focado: `.venv/Scripts/python.exe -m pytest tests/test_multimercado.py -q`
  → **15 passed in 0.12s** (RED confirmado antes: `ModuleNotFoundError:
  No module named 'tradingagents.desk.mercados'`).
- Áreas tocadas: `pytest tests/test_salas.py tests/test_multimercado.py
  tests/test_desk_conhecimento.py tests/test_desk_maestro.py -q`
  → **58 passed in 3.56s**.
- API de escola/salas: `pytest tests/test_service_escola.py -q`
  → **22 passed in 18.40s**.
- Lote amplo: `pytest tests/ -q -k "desk or sala or escola or piloto or
  multimercado or conhecimento or multimoeda or evo"`
  → **514 passed, 2 skipped, 1 failed** (a falha é a
  **AMBIENTAL** já documentada no E5: `test_main_backend_real_morre_e_
  avisa_com_log` encontra o app INSTALADO do dono na porta 8000 — PID
  13800; reproduz isolada; runner virgem do CI não afetado).
- `ruff check` nos 5 arquivos tocados → **All checks passed!**

## Ressalvas (honestidade)

- **Taxas/lotes B3/US são ESTIMATIVAS DE POLÍTICA** — a calibração
  definitiva é ato de homologação (adaptador + mesa de custos). Feriados
  são snapshot 2026 com revisão anual manual declarada como tal.
- **Nenhum adaptador novo; nenhuma execução não homologada** — classes
  não-cripto não abrem sala por construção (dois gates independentes:
  adaptador não suporta o mercado E `execucao_homologada=False`).
- **Sem UI nova e sem ciclo autônomo alterado** — E6 não adiciona página
  nem mexe no ciclo (cripto 24/7 inalterado; `pode_operar` é gancho de
  política com teste). A mudança em `app.py` é parâmetro OPCIONAL de rota;
  a homologação no bundle instalado fica para o próximo reempacotamento
  (nada de novo para o usuário final operar).
- Suíte completa local: `.venv/Scripts/python.exe -m pytest -q` →
  **1 failed, 2604 passed, 5 skipped, 71 subtests em 242,56 s**; a única
  falha é a mesma **AMBIENTAL** do launcher descrita acima (app do dono na
  porta 8000 — PID 13800; reproduz isolada, sem relação com os arquivos do
  E6); CI em runner virgem é o gate real (ADR-004).
- **Fechamento CI (push `87a14da`):** Windows Release Build run
  `38546817504` — **success** (artefato `dz23-windows-x64` = bundle do
  código E6) + Matriz do Instalador run `38546817421` — **success**
  (`TOTAL: 0 falha(s)`; artefato `matriz-instalador-evidencias`).
  Nenhum Release publicado (tag segue aguardando autorização do dono).
- LIVE `BLOCKED_BY_POLICY` o tempo todo. O app do dono NÃO foi tocado.
