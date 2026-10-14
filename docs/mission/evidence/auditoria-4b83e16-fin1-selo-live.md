# FIN-1 — Mesa recusa corretora LIVE sem selo de código (auditoria ChatGPT, commit 4b83e16)

**Achado (auditoria independente):** `tradingagents/brokers/ccxt_bridge.py`
contém modo LIVE e a chamada efetiva `create_order`; era preciso revisar
TODAS as condições para chegar a essa chamada. O caminho de PRODUÇÃO até
`create_order` é único — `desk/brokers.py::build_desk_broker` →
`ccxt_bridge.submit_order` — e ele construía corretora LIVE com apenas a
configuração salva pelo painel (`TRADINGAGENTS_DESK_BROKER=ccxt` +
`TRADINGAGENTS_BROKER_MODE=live` + frase do dono + tetos + chaves): faltava
a trava de MESA. O mandato da mesa é PAPER por construção, mas o par
(ponte LIVE, mandato PAPER) ficava incoerente — a última fechadura de
dinheiro real dependia de campos graváveis pelo painel.

**Correção (commit `e66f2b7`):** `build_desk_broker(..., live_authorized=False)`
recusa construir corretora LIVE sem autorização explícita de CÓDIGO
(`live_authorized=True`), que o painel NÃO concede; `_desk_boot` passa
`mandate.live_authorized` (False em PAPER); a recusa vira 409 verboso
(nunca 500 mudo); o checklist `/v1/live/status` declara o selo e
`pode_operar_real` nunca promete. Os freios da ponte (frase exata do dono,
chaves configuradas, teto por ordem, teto de perda diária) continuam
valendo POR CIMA — a correção não removeu nem afrouxou nenhum.

## Provas

| Evidência | Comando / resultado |
|---|---|
| RED (pré-fix) | `test_mesa_recusa_arrancar_em_live_mesmo_com_cfg_completa` falhava: "mesa arrancou com corretora LIVE (200)" |
| GREEN | `tests/test_auditoria_fin1_live_desk_seal.py` — **7 passed** (fresco, 2026-10-14) |
| Regressão | 237 testes verdes na época (lote desk/brokers/service) |
| Rede | Nenhuma — nenhuma ordem real em nenhum teste |

```
.venv/Scripts/python.exe -m pytest tests/test_auditoria_fin1_live_desk_seal.py -q
# 7 passed
```

## Limitações declaradas (honestidade)

1. **LIVE continua FECHADO por construção** (`BLOCKED_BY_POLICY`): não foi
   removido, foi cercado. Este achado não autoriza nem habilita operação
   real — apenas garante que nenhum caminho de produção construa corretora
   LIVE sem selo de código. Mantido inalterado desde então.
2. A revisão é do caminho de PRODUÇÃO da mesa (adaptador). Usar a ponte
   `CCXTBridge` diretamente fora do adaptador continua exigindo os freios
   próprios dela (frase/tetos) — documentado no ADR-006 e no guia
   `LIVE-DINHEIRO-REAL.md` (cinco fechaduras).

## Arquivos

- `tradingagents/desk/brokers.py` (`build_desk_broker` + selo)
- `service/app.py` (`_desk_boot`/`_desk_of`/`/v1/live/status`)
- `tradingagents/brokers/ccxt_bridge.py` (docstring corrigida);
  `docs/mission/TRADING-DECISIONS.md` (ADR-006)
- `tests/test_auditoria_fin1_live_desk_seal.py` (novo)
