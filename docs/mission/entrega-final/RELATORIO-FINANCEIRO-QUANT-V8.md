# DZ23 TRADING AI — RELATÓRIO FINANCEIRO E QUANTITATIVO (V8.1 §13.5)

> Para a auditoria independente. Objetivo: dar a base factual sobre **como o dinheiro (simulado) é tratado**,
> **o que é quantitativamente comprovado** e **o que deliberadamente não é afirmado**. Data: 2026-10-14.
> Complementa `RELATORIO-HOMOLOGACAO-V8.md` (B) e `MATRIZ-REQUISITOS-V8.md` (A).

## 1. Estado financeiro atual (sem ambiguidade)

- **Nenhuma operação real ocorreu.** LIVE bloqueado em camadas (código, interface, política).
- **Nenhum resultado real existe.** Todos os números do produto hoje são **PAPER** (simulação) ou dados de
  laboratório rotulados. Nenhuma projeção de lucro é feita ou permitida pela interface.
- **R$ 1.000** registrados apenas como *possível capital futuro* (`REGISTRO-CAPITAL-PILOTO.md`) — sem uso.
- O executor **não certifica** aprovação financeira (V8.1 §13) — esta é matéria da auditoria independente.

## 2. Arquitetura financeira (o que sustenta os números)

| Camada | Implementação | Propriedade garantida |
| :--- | :--- | :--- |
| Livro-caixa (ledger) | `tradingagents/desk/ledger.py` — partidas dobradas, Decimal | Soma fecha; `is_balanced()` é gate de operação; nada "executado" por intenção |
| Taxas e custos | Pipeline F3–F6 (`costs.py`, fees do bridge) | Taxas reais por corretora; custo nunca estimado no copiloto; lucro é sempre LÍQUIDO |
| Kernel de ciclo | K1–K6 (ordens/intents/execução) | Fills parciais, cancelamentos, reembolsos e eventos duplicados tratados |
| Reconciliação | FIN-3 — identidade DA CORRETORA (`clientOrderId` + resposta perdida) | Timeout após ordem aceita não perde nem duplica posição |
| Gates de autorização | FIN-2 (matriz adversarial: dono, conta, estratégia, kill-switch, limites, reconciliação) | Nenhuma ordem sem TODOS os gates; recusa com motivo |
| Freio diário | `ExecutionCoordinator.estado_do_dia()` (perda do dia × limite) | Parada diária ligada ao livro; bloqueia novas entradas |
| Posse exclusiva | `PosseDeExecucao` (vence em 15 min; renovação por ciclo) | Duas instalações não operam a mesma conta (anti-split-brain) |
| Procedência | `provenance.py` + origem do preço em cada payload | Todo número tem fonte e estado declarados |

## 3. Comprovação (baterias e evidências)

- Gates: `tests/test_fin2_*` (matriz adversarial por achado), `tests/test_fin3_*` (resposta perdida),
  `tests/test_kernel_*`, `tests/test_fees_*`, `tests/test_ledger_*` — todos verdes na suíte completa (3002).
- Auditoria por achado: `docs/mission/evidence/auditoria-4b83e16-{sec1,fin1,fin2,fin3}*.md`.
- Crash/concorrência: item 22 (30/30; reinício em operação) + C019 (corrida da posse em Postgres real no CI).
- PAPER ponta a ponta: C014 (ciclo real no bundle instalado; ledger balanceado; `pendências=[]`; reinício
  restaurando estado) e TD-5 (13/13 no bundle, incl. registro manual e alerta objetivo).

## 4. Camada quantitativa (o que existe e o que é proibido)

| Componente | Função | Honestidade embutida |
| :--- | :--- | :--- |
| Motor de quant/Monte Carlo | Análise de risco de cenários (V7) | Rótulos de simulação; não vira promessa |
| Walk-forward / fora da amostra | `evolucao.py`: janelas comuns + Bonferroni; controle anti-overfitting | Promoção automática SÓ em PAPER; recusa fora de PAPER; rollback |
| Champion/Challenger | Experimentos com parâmetros CONGELADOS (assinatura SHA-256) | IA não aprova a si; não sobe limites; não vai a LIVE |
| Escola de traders | Certificação por contexto (`corretora\|conta\|mercado\|modo`); SEM EVIDÊNCIA SUFICIENTE abaixo dos mínimos | Certificado não atravessa corretora; revogação por deterioração |
| Indicadores de desempenho (por trader) | `sala_trader.py`: P&L líquido, expectativa, hit rate, profit factor, drawdown, exposição, volatilidade, **Sharpe/Sortino por operação (não anualizado)** | Fórmula, origem, período, moeda e limitações documentados; `None` + motivo quando o número seria enganoso |
| Segregação de resultados | Painel global e salas | PAPER/TESTNET/LIVE **jamais** somados na mesma curva (pins de teste) |
| Taxa de abstenção | `decisao.py` (OPERAR/AGUARDAR/BLOQUEAR/SUSPENDER) + abstenções por agente no ciclo | Abstenção visível com motivo; nunca apresentada como lucro |

## 5. Resultados atuais (PAPER/laboratório — sem valor preditivo)

- Ciclos PAPER no bundle: 20 propostas → 2 admitidas → 2 entradas (C014/C021) — prova de **fiação**, não de
  mercado; dados de fixture rotulados.
- Feed real (item 16): 239 velas BTC/ETH com qualidade `ok`, snapshot velho recusado — prova de **integridade
  de dado**, não de rentabilidade.
- Nenhuma estratégia é apresentada como "rentável"; a régua central é "evidência suficiente ou SEM EVIDÊNCIA".

## 6. Achados de auditoria (F3–F6/K1–K6 + SEC/FIN) — situação

Todos os achados conhecidos (PR #9 / auditoria do commit `4b83e16`) foram tratados com causa-raiz e teste de
regressão (ver B §5); nenhum achado crítico permanece aberto. A análise F3–F6/K1–K6 foi concluída nos
checkpoints P0-02/P0-03 e revalidada nos testes acima.

## 7. Limitações declaradas (para a auditoria considerar)

1. Não há histórico real de operações — qualquer avaliação de rentabilidade real é impossível hoje.
2. Números de fixture não descrevem o mercado; o caminho real (dados + taxas + reconciliação) está provado,
   mas em escala de teste.
3. Listas de preços de IA e metadados de mercado têm data de referência e são estimativas de política.
4. Este relatório **não** é certificação financeira; é o insumo factual para a auditoria independente.
