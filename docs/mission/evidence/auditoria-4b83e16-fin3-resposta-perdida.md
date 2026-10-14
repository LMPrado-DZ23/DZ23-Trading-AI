# FIN-3 — Resposta perdida: reconciliação pela identidade DA CORRETORA (auditoria ChatGPT, commit 4b83e16)

**Achado (auditoria independente):** testar o cenário em que a corretora
ACEITA a ordem, mas a resposta de rede se perde; comprovar reconciliação
por clientOrderId, prevenção de duplicação e tratamento de estado
desconhecido.

**Defeito comprovado por sonda offline (antes do fix):** a mesa enviava a
ordem com o clientOrderId derivado pela ponte — `dz23-<sha256[:20]>` —
mas a reconciliação consultava a corretora com o coid INTERNO da mesa
(`coid-...`), id que a corretora nunca viu. Uma corretora honesta responde
`OrderNotFound` → ausência "confirmada" → registro vira `REJECTED` com a
reserva LIBERADA — sobre uma ordem VIVA na corretora — e os fills nunca
eram consultados. A sonda reproduziu o caso: ordem executada durante o
blecaute ficava irreconhecível e o capital voltava para a mesa.

**Correção (commit `aade521`):** a identidade do lado da corretora nasce
ANTES do envio (derivada pela própria ponte — sem reimplementar a
fórmula), viaja no `BrokerTimeout` quando o envio é ambíguo e é devolvida
no sucesso; `OrderRecord.external_client_id` é persistido no snapshot
(sobrevive a reinício) e adotado também quando a corretora confirma a
ordem; reconciliação, consulta de execuções e varredura de órfãs passam a
priorizar `external_id → external_client_id → coid da mesa → order_id`.
Ausência de VERDADE (a conexão caiu antes do envio) continua liberando a
reserva; estado desconhecido segue fail-closed com reserva mantida; nada
é reenviado em nenhum caminho.

## Cenários comprovados (`tests/test_auditoria_fin3_resposta_perdida.py`)

| Classe | O que prova |
|---|---|
| `TestEnvioAmbiguo` | resposta perdida fecha o portão e preserva a reserva; pedido novo recusado enquanto incerto; execução no blecaute é revivida por clientOrderId (consulta o `dz23-*` que a corretora conhece, NUNCA o coid interno); ordem viva NÃO é declarada ausente; estado desconhecido mantém reserva + portão; ausência confirmada de verdade libera |
| `TestNuncaDuplica` | replay do mesmo pedido não toca a corretora; reenvio direto com o mesmo clientOrderId é recusado pela corretora e classificado AMBÍGUO (fail-closed) — nada duplica |
| `TestExecucoesAdversariais` | fills que divergem do acumulado mantêm o portão fechado (`MISMATCH`); execução sem identificador não vira posição |
| `TestSobreviveAoReinicio` | a identidade sobrevive ao reinício: reconciliação pós-restart usa o MESMO clientOrderId e nenhuma ordem é reenviada |
| `TestNenhumaRedeNemOrdemReal` | guarda estrutural: a única corretora é a falsa, em testnet |

A suíte usa a ponte `CCXTBridge` e o adaptador `CCXTDeskBroker` de
PRODUÇÃO (gate de capital, journal, derivação do coid) com a exchange
substituída por uma corretora falsa binance-ish — sem rede, sem ordem real.

## Provas

| Evidência | Comando / resultado |
|---|---|
| RED (pré-fix, sonda offline) | ordem viva marcada como ausente + reserva liberada; 5 falhas de comportamento na suíte nova |
| GREEN | `tests/test_auditoria_fin3_resposta_perdida.py` — **12 passed** (fresco, 2026-10-14) |
| Regressão | **287 passed** nas 11 suítes afetadas (crash/restart, caminho único, desk execução/scheduler, auditorias, ordens órfãs, capacidades, fees, motor de saída, ativo descoberto) |
| Ruff | `ruff check` nos 3 arquivos — All checks passed |
| Suíte completa | `2627 passed / 5 skipped / 71 subtests` (exit 0; excl. `tests/windows` — e2e Playwright pulado por dependência local não instalada) |
| tests/windows | 56 passed + **1 falha AMBIENTAL conhecida** (`test_main_backend_real_morre_e_avisa_com_log`: o app INSTALADO do dono responde na porta 8000 nesta estação — a sonda encontra o servidor REAL; o app do dono não foi tocado) |

```
.venv/Scripts/python.exe -m pytest tests/test_auditoria_fin3_resposta_perdida.py -q
# 12 passed
```

## Limitações declaradas (honestidade)

1. **Registros legados:** ordens gravadas no OMS ANTES deste fix restauram
   com `external_client_id` vazio — a reconciliação delas ainda consulta o
   coid interno. Nas ordens vivas/preenchidas os cruzamentos de órfã e de
   posição mantêm o portão fechado (não libera capital à toa), mas o
   rótulo `REJECTED` indevido continua possível para esses registros
   antigos — reconciliar manualmente contra o extrato da corretora.
2. A corretora falsa usa semântica binance-ish (`clientOrderId`); OKX
   (`clOrdId`) é tratado pela ponte mas não é exercitado nesta suíte.
3. A consulta de fills só consegue deduplicar o que tem id — execução sem
   identificador é tratada como visão incompleta e mantém o portão fechado
   (por desenho, não é promovida a posição).

## Arquivos

- `tradingagents/desk/execution.py` (identidade da corretora no registro,
  no `BrokerTimeout`, na reconciliação, nos fills e no snapshot/restore)
- `tradingagents/desk/brokers.py` (coid derivado antes do envio; devolvido
  no sucesso; carregado no timeout ambíguo)
- `tests/test_auditoria_fin3_resposta_perdida.py` (novo, 12 testes)
