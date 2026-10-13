# Repositórios candidatos: valor para o DZ23

## Decisão executiva

O TradingAgents DZ23 já possui uma base própria para agentes LangGraph, CCXT, Risk Engine, Paper/Demo, MT5 Adapter, Event Log, Trading Floor read-only, backtest e workers. Portanto, a estratégia correta é **complementar**, não substituir o núcleo por vários frameworks concorrentes.

O CCXT já é o candidato efetivamente utilizado no projeto e deve continuar encapsulado no Exchange Adapter do DZ23. Os demais projetos devem ser classificados como referência, laboratório offline ou serviço separado, somente quando existir uma lacuna concreta e mensurável.

> Nenhum agente, biblioteca, EA, backtest, engine ou dashboard garante lucro, crescimento da carteira ou preservação de capital.

## Matriz de decisão

| Projeto | Situação recomendada | Valor principal | Decisão |
|---|---|---|---|
| CCXT | Já utilizado | Conectividade crypto | Manter, atualizar com pin de versão e fortalecer testes/reconciliação. |
| NautilusTrader | Não integrado | Engine event-driven, backtest, adapters e recovery | Referência; piloto isolado somente se houver gap concreto de venue/execução. |
| Hummingbot | Não integrado | Market making, arbitragem, conectores CEX/DEX | Serviço separado e subordinado ao DZ23 apenas para um caso crypto comprovado. |
| TauricResearch/TradingAgents | Não integrado como motor | Pesquisa multiagente e debates | Referência ou worker read-only; o DZ23 já possui função semelhante. |
| FinRL | Não integrado | Laboratório de reinforcement learning | Worker offline de pesquisa, sem credenciais e sem execução direta. |
| VectorBT | Não integrado | Sweeps e backtest vetorizado | Spike offline condicionado à licença Commons Clause e comparação com baseline. |
| Backtrader | Removido das dependências | Benchmark clássico de backtest | Auditoria open source (2026-10-13): constava no pyproject sem uso no código e é GPLv3+, incompatível com a Apache-2.0 do projeto — dependência removida do pyproject/uv.lock. |
| Freqtrade | Não integrado | Lifecycle de bot crypto e stoploss | Principalmente referência; serviço separado somente para gap mensurável. |
| LumiBot | Não integrado | Adapters para brokers/ativos adicionais | Bloqueado até resolver divergência GPLv3 versus MIT. |
| MT5-Python-Trading | Não integrado | Exemplos de API MT5 | Referência apenas; não há licença declarada. |
| geraked/metatrader5 | Não integrado | Indicadores e EAs MQL5 | Portar apenas sinais selecionados; não executar EAs diretamente na conta DZ23. |
| TradersPost/metatrader5 | Não integrado | Exemplo MT5 → webhook | Referência de payload; não possui idempotência, reconciliação ou licença declarada. |
| Claw3D | Não integrado profundamente | UX/visual 3D | Referência ou serviço read-only; o DZ23 já tem Trading Floor inicial. |
| OpenClaw Office | Não integrado | UX isométrica de workflow | Referência visual; preferir ampliar a Trading Floor existente. |
| Polymarket/py-clob-client | Não usar | Cliente histórico Polymarket CLOB | Bloquear: upstream arquivado e declarado não funcional. |

## O que deve permanecer como autoridade

A arquitetura deve ter uma única cadeia por conta:

```text
research/agentes → sinal tipado → Risk Engine DZ23 → autorização →
CCXT ou MT5 Adapter → Event Log → reconciliação → Trading Floor read-only
```

Nenhum framework externo pode assumir simultaneamente o controle de risco, execução, posições e reconciliação da mesma conta. Nunca operar dois engines de trading concorrentes na mesma conta.

O Event Log deve registrar intenção, evidências, versão de dados/modelo/prompt, aprovação ou rejeição, client-order-id, resposta do venue, retries, cancelamentos, fills, fees e reconciliações.

## Prioridades

### P0 — fortalecer o que já existe

Manter CCXT dentro do adapter atual. Adicionar pin de versão, testes por exchange/produto, validação de precisão, idempotência, tratamento de timeout, consulta após resposta ambígua, fills parciais, reconexão, reconciliação e kill switch.

### P1 — testar motores externos apenas se houver lacuna

NautilusTrader ou Hummingbot podem ser avaliados em serviço separado, Paper/Demo/Testnet e conta isolada. O piloto deve ter anti-corruption layer, eventos normalizados, healthcheck, reconciliação, rollback e desligamento completo antes de qualquer cutover.

### P2 — ampliar pesquisa

TradingAgents pode fornecer um research worker não vinculante. FinRL pode treinar políticas offline. VectorBT pode acelerar sweeps. Todos devem produzir artefatos versionados e sinais candidatos, sem acesso a credenciais ou brokers.

### P3 — referências e UX

Claw3D e OpenClaw Office podem orientar a estética do escritório virtual. A interface deve consumir somente estado reconciliado. Nenhuma animação, transcript, balão ou painel pode provar que uma ordem foi aceita ou preenchida.

## Bloqueios jurídicos

- `Backtrader`: GPLv3+; não incorporar sem análise jurídica.
- `Freqtrade`: GPLv3; preferir referência ou processo separado.
- `NautilusTrader`: LGPL-3.0-only; revisar linking, modificações e distribuição.
- `VectorBT`: Apache 2.0 com Commons Clause; revisar especialmente uso comercial/SaaS.
- `LumiBot`: divergência entre `LICENSE` GPLv3 e declarações MIT no README/setup; não reutilizar até resolver.
- `MT5-Python-Trading`: sem licença declarada; não copiar.
- `TradersPost/metatrader5`: sem licença declarada e com código JSON derivado cuja proveniência requer revisão.
- `geraked/metatrader5`: MIT, mas alguns assets/códigos possuem autoria de terceiros; auditar individualmente.
- Claw3D/OpenClaw Office: MIT, mas auditar modelos, sprites, imagens, fontes e dependências.

## Conclusão

Não devemos adicionar os 15 repositórios ao projeto. Isso criaria duplicação, divergência de estado, conflitos de licença e múltiplos caminhos de execução.

A melhor composição é:

```text
DZ23 = agentes, governança, Risk Engine, Event Log, reconciliação e execução oficial
CCXT/MT5 = adapters oficiais por conta e venue
TradingAgents/FinRL/VectorBT = pesquisa offline opcional
NautilusTrader/Hummingbot = pilotos externos somente quando houver lacuna
Claw3D/OpenClaw Office = referência ou projeção visual read-only
```

## Fontes auditadas

- [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)
- [AI4Finance-Foundation/FinRL](https://github.com/AI4Finance-Foundation/FinRL)
- [mementum/backtrader](https://github.com/mementum/backtrader)
- [polakowo/vectorbt](https://github.com/polakowo/vectorbt)
- [nautechsystems/nautilus_trader](https://github.com/nautechsystems/nautilus_trader)
- [ccxt/ccxt](https://github.com/ccxt/ccxt)
- [freqtrade/freqtrade](https://github.com/freqtrade/freqtrade)
- [hummingbot/hummingbot](https://github.com/hummingbot/hummingbot)
- [Lumiwealth/lumibot](https://github.com/Lumiwealth/lumibot)
- [Polymarket/py-clob-client](https://github.com/Polymarket/py-clob-client)
- [ntungufhadzeni/MT5-Python-Trading](https://github.com/ntungufhadzeni/MT5-Python-Trading)
- [geraked/metatrader5](https://github.com/geraked/metatrader5)
- [TradersPost/metatrader5](https://github.com/TradersPost/metatrader5)
- [iamlukethedev/Claw3D](https://github.com/iamlukethedev/Claw3D)
- [wickedapp/openclaw-office](https://github.com/wickedapp/openclaw-office)
