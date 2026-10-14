# Missão obrigatória Qoder — 10 traders especializados (2026-10-13)

**Decisão do proprietário:** expandir os 3 traders operacionais atuais para uma arquitetura extensível com **10 perfis especializados**. O supervisor determina elegibilidade e ativação por regime; não executar os dez cegamente. Aplicativo Windows local, single-owner, provedores de IA por APIs externas. LIVE permanece desabilitado.

## Perfis exigidos
1. Tendência (existente)
2. Reversão à média (existente)
3. Rompimento (existente)
4. Scalping
5. Swing trading
6. Momentum
7. Volatilidade
8. Arbitragem entre corretoras (paper, cotações executáveis sincronizadas e custos totais)
9. Estatístico (mean-reversion/pairs quando dados adequados)
10. Adaptativo por regime (parâmetros versionados e promoção aprovada)

## Critérios antes de chamar de implementado
- Auditar `tradingagents/desk/traders.py`, `orchestrator.py`, `scheduler.py`, `risk.py`, contratos de sinais, positions, fees e testes existentes; reutilizar abstrações sem quebrar os três traders.
- Cada trader tem identificador estável, universo/mercados e timeframe, requisitos mínimos de dados, regime de elegibilidade, condições determinísticas de BUY/SELL/HOLD/NO_TRADE, entrada, saída, expiração do sinal, invalidação, custos, proteção e explicação auditável.
- Aplicar o **mesmo** contrato de estratégia no backtest e paper; sem overfitting/lookahead; medir retorno líquido, expectancy, drawdown, PF, estabilidade temporal e incerteza. Não tratar quantidade de traders como prova de lucro.
- Supervisor escolhe traders habilitados conforme regime e evidências; define orçamento global e por estratégia; resolve sinais conflitantes, duplicações entre exchanges e concentração. Risco determinístico pode vetar qualquer trader. LLM não envia ordens nem ajusta permissões.
- Evolução gradual: 3 atuais preservados, sete novos implementados com fixtures determinísticas e testes individuais/adversariais; traders sem vantagem estatística ficam em pesquisa/PAPER, não são promovidos.
- Para arbitragem, usar preços contemporâneos executáveis, livro/latência, inventário e taxas de saque/transferência; sem alegar arbitragem lucrativa sem prova. Sem alavancagem ou fundos reais.
- Dashboard PT-BR: lista dos 10 traders, estado (pesquisa/validado/paper/pausado/bloqueado), modelo IA, operações e motivo de NO_TRADE; permitir habilitar somente quando elegível.
- Reproduzir CI, testes de integração e E2E; executar realmente em PAPER, registrar logs de sinal→risco→fill→ledger→reconciliação.
- Atualizar checkpoints `docs/mission/`, evidências, SHA, CI e status real. Não marcar 10/10 homologados só por existência de classes.

## Ordem
Auditoria do núcleo -> contratos/versionamento -> implementação incremental dos sete perfis -> supervisor de regime e orçamento -> teste adversarial -> paper E2E -> painel -> CI -> evidências.
Não realizar merge, deploy ou LIVE sem gates e autorização específica.
