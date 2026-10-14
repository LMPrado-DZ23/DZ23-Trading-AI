# V8 §5 — "Notificações repetidas como ord-3": investigação definitiva + fix da entrega duplicada (commit `f7f1f0f`)

**Achado (Prompt Master V8, §5):** "Investigar definitivamente
notificações repetidas como `ord-3`."

## Inventário completo dos mecanismos de repetição (antes de concluir)

O produtor e o entregador foram varridos por inteiro — cada caminho que
poderia re-exibir a mesma notificação:

| # | Mecanismo | Veredito | Evidência |
|---|---|---|---|
| a | Produtor re-enfileira ordem antiga a cada ciclo | **Excluído** | `scheduler.run_once` monta `out["ordens"]` só com as submissões do ciclo atual; comportamento idêntico desde a introdução (`git log -S` → `30b8683` E2 / `7a1da08` unificação) |
| b | Reincidência do mesmo alerta vira nova entrega | **Excluído (dentro da janela)** | `FilaDeAlertas.enfileirar` deduplica por chave em 3600 s — reincidência incrementa `ocorrencias`; sem nova entrega |
| c | Despacho sequencial entrega 2× o mesmo alerta | **Excluído** | primeiro canal `ok` → `marcar_entregue` + `break`; estados `entregue` não voltam a `pendentes()` |
| d | `Notificador` antigo (`alerts.py`, silêncio de 30 min) ainda ligado | **Excluído (código morto)** | `_notificador()` em `service/app.py` não tem NENHUM chamador; o único caminho vivo é a fila E2 |
| e | **Despacho concorrente da MESMA fila** | **DEFEITO COMPROVADO** | sem nenhuma trava, três threads despacham a mesma `FilaDeAlertas`: ciclo da mesa (`service/app.py:2440`), copiloto manual (`:2075`) e rota `POST /v1/alertas/verificar` (`:3604`) |

**Mecanismo (e):** dois despachos concorrentes leem o mesmo alerta ainda
`novo` em `pendentes()` — antes de qualquer um marcar `entregue` — e ambos
entregam a MESMA notificação. Não existe lock em `notificacao_viva.py`
(verificado por varredura) e o `Despachante` é construído por chamada, mas
a fila compartilhada é única por inquilino (`_FILAS_ALERTAS`).

## Prova determinística

Teste novo com **barreira**:
`tests/test_centro_de_alertas.py::TestFilaNucleo::test_despacho_concorrente_nao_entrega_o_mesmo_alerta_duas_vezes`
— duas threads despacham a mesma fila; o canal falso bloqueia na barreira
até a segunda thread chegar, forçando a sobreposição exatamente onde ela
importa (ambas já passaram por `pendentes()`).

- **RED (pré-fix):** `assert 2 == 1` — o MESMO alerta (mesmo `id`,
  `01f15a4b8d67`) recebeu dois envios e duas tentativas `ok=True`.
- **GREEN (pós-fix):** 1 envio.
- **Red-green no artefato final:** trava removida temporariamente → falha
  em 0,39 s; restaurada → passa em 3,23 s (a barreira vence por timeout no
  caminho serializado — por desenho do teste, não é flake).

## Correção (commit `f7f1f0f`)

1. `FilaDeAlertas.trava` — `threading.Lock` POR FILA (por inquilino).
2. `Despachante.despachar()` — o corpo roda dentro da seção crítica
   (`_despachar_interno`), serializando o despacho da mesma fila; filas de
   inquilinos distintos seguem despachando em paralelo.
3. `_persistir_fila_alertas` (`service/app.py`) — o snapshot (`to_dict`)
   é tirado sob a mesma trava: gravar durante um despacho em outra thread
   serializaria estados no meio da entrega.

Nada foi afrouxado: dedupe, backoff, rate limit, expiração e auditoria
permanecem idênticos; a trava só impede a entrega simultânea.

## Provas (frescas, 2026-10-14)

| Evidência | Resultado |
|---|---|
| Teste com barreira (RED pré-fix) | falha `2 == 1` — duplicação reproduzida |
| Red-green no artefato final | trava fora → falha 0,39 s; trava dentro → passa |
| `tests/test_centro_de_alertas.py` | **42 passed** |
| Suíte completa (excl. `tests/windows`) | **2628 passed / 5 skipped / 71 subtests**, exit 0 |
| Ruff (`notificacao_viva.py`, `service/app.py`, teste) | All checks passed |
| Push | `74c319d..f7f1f0f` na branch `fix/instalador-atualizacoes-windows` |

## Limitações declaradas (honestidade)

1. **A repetição HISTÓRICA (ord-3) não é reconstruível de logs locais.**
   O store do dono (`%APPDATA%\DZ23-Trading-AI\`) NÃO tem `alertas.json` —
   a fila E2 nunca persistiu um alerta real nesta máquina — e o registro
   do Windows mostra a identidade DZ23 com `Enabled=0` (notificações do
   app desligadas pelo dono; `LastNotificationAddedTime`
   2026-10-14T09:56:33Z, coerente com clique manual em "Testar" no exe
   pós-N01). Nenhum arquivo do dono foi modificado. O que ficou
   COMPROVADO: a corrida de despacho existia no código e produz exatamente
   o sintoma "mesma notificação repetida" — reproduzida e corrigida.
2. **Condição persistente re-notifica a cada 1 h** (janela de dedupe) —
   silêncio deliberado por desenho ("o mesmo problema não vira 200
   mensagens"); não é bug e não foi alterado.
3. **Falha RELATADA pelo canal depois do toast já exibido** (ex.:
   PowerShell estourando o timeout de 20 s com o toast na tela, ou
   `returncode ≠ 0` pós-exibição) entra em backoff e reexibe na próxima
   tentativa — duplicar é preterível a sumir em silêncio
   (`notificacao_viva.py`, "alerta nunca some em silêncio"). Cenário raro:
   o script mostra o toast e sai rápido; não alterado nesta rodada.
4. **Criação concorrente da fila** (`_fila_alertas` get-or-create em
   `service/app.py`) pode, em tese, instanciar duas filas antes de a
   primeira existir (duas primeiras requisições simultâneas no boot) —
   fora do escopo deste achado (entrega duplicada); registrado para um
   próximo ciclo de endurecimento.

## Arquivos

- `tradingagents/desk/notificacao_viva.py` (trava por fila; seção crítica
  no despacho)
- `service/app.py` (snapshot da persistência sob a trava)
- `tests/test_centro_de_alertas.py` (teste de concorrência com barreira)
