# TradingAgents Operável — deploy no Coolify (3 passos, 5 minutos)

Sistema COMPLETO num serviço só: API + painel de CONFIGURAÇÃO + painel de
OPERAÇÃO embutidos. Depois deste deploy, TUDO se faz pelo navegador.

## 1. Criar o recurso
Coolify → + New Resource → Dockerfile → aponte para esta pasta
(ou suba a um repo Git privado e aponte para ele).

## 2. Definir UMA variável e UM volume
- Environment Variables:  ACCESS_PASSWORD = (uma senha forte tua)
- Storages/Volumes:       /data   (persistente — guarda config e histórico)

## 3. Domínio e Deploy
Ex.: tradingagents.dz23.online → Deploy.

## Usar (para sempre)
Abrir o domínio → digitar a senha → aba ⚙️ Configurações: preencher as
chaves (IA, FRED, Alpha Vantage, exchange) → 💾 Salvar (vale no próximo job,
SEM reiniciar nada) → aba 📈 Operação: NVDA + data → ▶ Analisar agora →
acompanhar o card até CONCLUÍDA e ler a decisão.

## Segurança
- Uma senha (Bearer) protege painel e API; /healthz é o único público.
- Chaves ficam em /data/config.json (0600) no SERVIDOR; na tela, sempre
  mascaradas (•••últimos4). /docs desligado.
- Corretora: seleção multi-exchange (Binance…Mercado Bitcoin) com modo
  travado em TESTNET; execução real de ordens só após o marco de broker
  CCXT ser implementado e auditado (MISSAO-BROKER-CCXT.md).
