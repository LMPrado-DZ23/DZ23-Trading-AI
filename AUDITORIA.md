# Guia para auditoria independente

Este documento existe para **encurtar o caminho do auditor até os pontos
fracos**. Auditoria que só encontra elogio não protege ninguém.

## Como verificar em 3 comandos
```bash
pip install -e ".[desk,dev]"
ruff check .          # esperado: All checks passed!
pytest -q             # esperado: 1279 passed / 2 skipped / 71 subtests
```

## O que foi construído (e onde olhar)

| Área | Arquivo | O que provar |
|---|---|---|
| Custos reais | `tradingagents/desk/costs.py` | taxa por exchange, spread por liquidez, slippage por tamanho; gate de 1,5× |
| Validação | `tradingagents/desk/validation.py` | walk-forward sem lookahead, custos descontados |
| Sizing/correlação | `tradingagents/desk/sizing.py` | tamanho por volatilidade; correlação presumida alta |
| IA de contexto | `tradingagents/desk/context.py` | só veta/reduz; nunca aumenta |
| Sinais/regime | `tradingagents/desk/signals.py` | indicadores, regime, confirmação multi-prazo |
| Alertas | `tradingagents/desk/alerts.py` | regras puras, anti-ruído |
| Estado durável | `tradingagents/desk/storage.py` | escrita atômica, Postgres externo |
| Mesa | `desk/{orchestrator,risk,execution,ledger,scheduler}.py` | reserva atômica, 11 estados, partidas dobradas |
| Cobrança | `tradingagents/billing/` | webhook RSA sobre corpo bruto, idempotência |

## Onde eu atacaria se fosse o auditor

Pontos que considero **genuinamente frágeis** — não os esconda:

1. **As estratégias não têm vantagem comprovada.** Média móvel, z-score e
   rompimento são de livro e já arbitrados. A engenharia é sólida; a
   vantagem é a pergunta em aberto. Nenhum backtest com dados reais foi
   rodado ainda (o ambiente de desenvolvimento não alcança as exchanges).
2. **Correlação é presumida, não medida.** `sizing.py` usa uma tabela
   conservadora. O correto seria calcular do histórico real — está no
   backlog, não no código.
3. **Slippage é modelo, não medição.** Conservador de propósito, mas não
   substitui comparar preço esperado vs. preço executado em produção.
4. **A IA de contexto depende do provedor do cliente.** Sem provedor, a mesa
   opera sem verificação (marcando isso). É escolha deliberada — provedor
   caído não pode parar a mesa — mas é uma lacuna real de cobertura.
5. **Modo live nunca foi exercitado contra uma exchange real.** As travas
   estão testadas com dublês; a primeira ordem real é um evento não coberto
   por teste.
6. **Aprendizado precisa de 12 operações fechadas por regime** para mudar
   comportamento. Em baixa frequência, isso pode levar meses.
7. **`tradingagents/agents/utils/memory.py` mantém o padrão antigo de
   temporário fixo** (`.with_suffix(".tmp")`). Não foi alterado por ser
   código do núcleo auditado (Gate A) cujos testes passam — mas tem a mesma
   classe de corrida que corrigimos em `desk/`. Vale revisar.

## Invariantes que NÃO podem ser quebrados
- Nenhuma ordem real sem: frase de autorização exata + teto por ordem +
  teto de perda diária + preço de referência conhecido.
- O gate de capital (Gate A) é incontornável, inclusive em live.
- Segredos nunca voltam pela API, nem em log, journal, backup ou painel.
- A IA de contexto nunca aumenta exposição nem libera veto de risco.
- Toda tabela e todo estado são isolados por `tenant_id`.

## Escopo honesto
Software para o cliente operar a **própria** conta com as **próprias**
chaves. Não gere dinheiro de terceiros e não prometa retorno: isso muda a
natureza regulatória da atividade. O padrão é paper/testnet.
