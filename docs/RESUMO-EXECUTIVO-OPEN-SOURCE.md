# Resumo Executivo — TradingAgents DZ23

## Visão geral

O **TradingAgents DZ23** é um projeto open source para construir uma agência multiagente de pesquisa e operação de trading. A plataforma combina agentes especializados de inteligência artificial, debate estruturado, gestão determinística de risco, execução governada, reconciliação, auditoria e observabilidade em um único sistema operacional.

O projeto foi desenhado para separar claramente **análise**, **decisão**, **risco** e **execução**. Essa separação reduz o risco de que uma resposta textual de um modelo de linguagem seja interpretada incorretamente como uma ordem, um fill, uma posição ou um resultado financeiro confirmado.

> O sistema é uma infraestrutura de pesquisa e automação financeira. Não é recomendação de investimento, não garante lucro e não elimina riscos de mercado, execução, tecnologia ou regulação.

## Problema que o projeto resolve

Sistemas de trading baseados em inteligência artificial frequentemente combinam modelos, dados e execução sem contratos verificáveis entre essas camadas. Isso dificulta responder perguntas essenciais: qual agente produziu uma decisão, quais evidências foram usadas, qual limite de risco foi aplicado, qual ordem foi enviada, qual fill ocorreu e se o estado local coincide com o estado da corretora.

O TradingAgents DZ23 aborda esse problema por meio de contratos tipados, trilhas de auditoria, controles fail-closed, reconciliação explícita e modos operacionais separados. O objetivo é tornar o comportamento do sistema observável, reproduzível e auditável antes de qualquer expansão para ambientes financeiros mais sensíveis.

## Proposta de valor

A plataforma oferece uma base extensível para organizações, pesquisadores e desenvolvedores que desejam experimentar agentes de trading sem começar diretamente por execução com dinheiro real. Ela permite desenvolver estratégias, comparar decisões de agentes, executar Paper Trading, acompanhar Shadow Trading e preparar uma integração controlada com contas MetaTrader 5 Demo.

A principal diferenciação técnica é que a inteligência artificial não possui autoridade direta sobre a corretora. Os agentes produzem análises e propostas estruturadas. O **Risk Engine** valida limites. O **ExecutionRouter** valida a coerência entre decisão e ordem. O **KillSwitch** pode bloquear a execução. A reconciliação verifica se o estado observado corresponde ao estado esperado.

## Arquitetura funcional

O fluxo central da plataforma é:

```text
Dados de mercado
    ↓
Agentes de pesquisa e análise
    ↓
Debate estruturado e comitê
    ↓
Decisão tipada
    ↓
Risk Engine determinístico
    ↓
ExecutionRouter
    ↓
Paper, Shadow ou MT5 Demo
    ↓
Fills, posições e PnL
    ↓
Reconciliação, auditoria e observabilidade
```

Os agentes podem ser organizados por desks de pesquisa, estratégia, risco, portfólio, execução e monitoramento. Cada reunião possui participantes, quorum, votos, evidências, status e eventos auditáveis.

## Modos operacionais

| Modo | Finalidade | Envia ordens? | Situação atual |
| --- | --- | ---: | --- |
| **Paper Trading** | Simular execução com ledger persistente | Não | Disponível |
| **Shadow Trading** | Registrar intenções e comparar resultados hipotéticos | Não | Disponível |
| **MT5 Demo** | Validar integração com conta de dinheiro virtual | Sim, somente no ambiente Demo | Requer terminal e conta externos |
| **Live Trading** | Operar dinheiro real | Não | Bloqueado nesta versão |

Os padrões publicados mantêm `LIVE_TRADING=false` e `REAL_MONEY_EXECUTION=false`. Esses controles não são substituídos por decisões de agentes ou comandos do frontend.

## Segurança e governança técnica

A plataforma adota uma política **fail-closed**. Quando a reconciliação é desconhecida, divergente, ausente ou desatualizada, a execução permanece bloqueada. O cliente não pode declarar que a reconciliação foi aprovada; o estado seguro precisa ser calculado no servidor.

A versão atual inclui autenticação multi-tenant, memberships, papéis, sessões assinadas, expiração e revogação de tokens. Os papéis disponíveis são Viewer, Analyst, Operator, Risk Manager, Admin e Auditor. A configuração suporta PostgreSQL para identidade e memberships quando `DATABASE_URL` está disponível, com SQLite como fallback local de desenvolvimento.

As principais salvaguardas são:

| Controle | Objetivo |
| --- | --- |
| Risk Engine | Aplicar limites determinísticos antes da execução |
| ExecutionRouter | Validar decisão, ordem, risco, conta e ambiente |
| KillSwitch | Bloquear execução de forma persistente |
| Reconciliação | Comparar ordens, fills, posições e saldo |
| Event Bus | Distribuir e reproduzir eventos de domínio |
| Auditoria | Registrar ações e eventos com rastreabilidade |
| Idempotência | Evitar ordens ou fills duplicados |
| RBAC | Separar permissões por usuário e tenant |
| CI e testes | Detectar regressões antes da publicação |

## Stack e deployment

O backend é construído em Python com FastAPI. O deployment Enterprise é descrito em Docker Compose e inclui a aplicação, PostgreSQL, Redis, Qdrant e Ollama opcional. O PostgreSQL armazena a identidade multi-tenant quando configurado. Redis está disponível para cache e filas futuras. Qdrant está disponível para memória vetorial e recuperação de conhecimento.

A integração MetaTrader 5 utiliza a biblioteca oficial quando o terminal e a conta Demo estiverem disponíveis. A ausência desses recursos externos não é mascarada por mocks: o gate de validação real permanece explicitamente pendente até que o ambiente autorizado seja executado.

## Estado atual do projeto

O branch de desenvolvimento contém a fundação operacional, o núcleo multi-tenant, a preparação open source e a documentação de deployment. A suíte automatizada atual foi executada em ambiente sanitizado com **928 testes aprovados, 2 testes ignorados e 71 subtestes aprovados**. Também foram executados smoke tests da API, smoke test multi-tenant, compilação Python e validação estrutural do Compose.

Esses resultados comprovam a qualidade do código testado no ambiente disponível. Eles não substituem a validação de Docker no computador de deployment, a conexão com PostgreSQL real, a integração com terminal MetaTrader 5 Demo, testes de TLS, backups de produção ou validação regulatória.

## Roadmap recomendado

A próxima etapa prioritária é executar o Compose em um host autorizado e validar o ciclo de inicialização, persistência, reinício e recuperação de PostgreSQL, Redis e Qdrant. Em seguida, deve ser concluída a migração do ledger operacional de jobs para PostgreSQL antes de escalar horizontalmente.

Depois disso, o projeto deve validar o adapter MT5 em conta Demo, implementar testes de integração pós-fill, fortalecer o onboarding multi-tenant, concluir a interface de usuário para administração de tenants e adicionar monitoramento de produção. A operação Live não faz parte do release atual e deve permanecer bloqueada até revisão técnica, operacional, jurídica e regulatória independente.

## Benefícios para a comunidade open source

A abertura do projeto permite que pesquisadores e desenvolvedores revisem contratos, testes, controles de risco, adapters e documentação. A comunidade pode contribuir com novas fontes de dados, estratégias, conectores de corretoras, melhorias de observabilidade, testes de segurança e interfaces de usuário sem remover as salvaguardas centrais.

O projeto inclui documentação de contribuição, política de segurança, código de conduta, templates de issues e pull requests, Dependabot e CI público. Contribuições que alterem execução, risco, autenticação ou isolamento de tenants devem incluir testes e explicação dos impactos.

## Mensagem para apresentação

> **TradingAgents DZ23 é uma plataforma open source para pesquisa e operação controlada de trading com múltiplos agentes de IA. Ela organiza agentes especializados em uma agência auditável, separa inteligência de execução, aplica risco determinístico antes de qualquer ordem e oferece Paper Trading, Shadow Trading, preparação para MT5 Demo e operação multi-tenant. O projeto prioriza verificabilidade, segurança fail-closed e colaboração aberta, mantendo a execução com dinheiro real bloqueada por padrão.**

## Referências

[1]: https://github.com/LMPrado-DZ23/tradingagents-dz23 "TradingAgents DZ23 — repositório do projeto"

[2]: https://github.com/TauricResearch/TradingAgents "TauricResearch TradingAgents — projeto upstream"

[3]: https://www.mql5.com/en/docs/python_metatrader5 "MetaTrader 5 Python integration documentation"

[4]: https://www.apache.org/licenses/LICENSE-2.0 "Apache License 2.0"
