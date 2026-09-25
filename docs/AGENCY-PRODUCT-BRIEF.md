# DZ23 AI Agency — Product Brief

## Visão

A DZ23 evoluirá de uma mesa multiagente de trading para uma **agência geral de negócios assistida por IA**, capaz de receber objetivos, decompor trabalho, executar tarefas em paralelo, consultar fontes autorizadas, produzir entregáveis e solicitar aprovação humana em ações sensíveis. O trading será um módulo especializado dentro da agência, inicialmente restrito a Paper/Testnet.

## Nível de rigor

O produto será tratado como **L4 para o módulo financeiro** e **L3 para os módulos gerais**, porque combina automação contínua, dados potencialmente sensíveis e decisões que podem gerar impacto financeiro ou reputacional. Nenhum agente poderá habilitar dinheiro real por conta própria.

## Usuário principal

O usuário é o proprietário/operador da agência. Ele define objetivos, delega missões, acompanha agentes, revisa resultados, aprova ações sensíveis e interrompe qualquer execução por meio de um kill switch independente.

## Módulos iniciais

| Módulo | Responsabilidade | Pode agir externamente? |
| --- | --- | --- |
| Orquestração | Decompor objetivo, priorizar, atribuir e consolidar | Não sem aprovação |
| Pesquisa | Coletar evidências, comparar fontes e produzir briefings | Leitura autorizada |
| Inteligência de mercado | Notícias, indicadores, concorrentes e sinais | Leitura autorizada |
| Operações | Checklists, processos, agenda e acompanhamento | Somente ações aprovadas |
| Conteúdo | Rascunhos, calendário e variações de comunicação | Publicação exige aprovação |
| Financeiro | Orçamentos, custos e relatórios | Nenhum pagamento automático |
| CRM | Qualificação e acompanhamento de leads | Comunicação exige política/aprovação |
| Risco e compliance | Avaliar impacto, permissões e políticas | Pode bloquear; não pode liberar |
| Trading | Pesquisa, sinais, risco, Paper/Testnet e reconciliação | Live permanece bloqueado |

## Fluxo principal

1. O usuário cria um objetivo.
2. O supervisor gera um plano com tarefas, dependências, orçamento e nível de risco.
3. Agentes especializados executam tarefas independentes em paralelo.
4. O agente de evidência registra fontes, hashes, timestamps e confiança.
5. O agente de risco revisa o plano e pode bloquear ou reduzir escopo.
6. Um sintetizador produz o resultado e lista pendências.
7. Ações externas ficam em estado `AWAITING_APPROVAL` até aprovação humana.
8. O sistema registra tudo em trilha de auditoria e atualiza métricas.

## Critérios de aceite da primeira fase

| Critério | Evidência esperada |
| --- | --- |
| Objetivo pode ser decomposto em tarefas | API, persistência e teste de contrato |
| Tarefas independentes executam em paralelo | teste com agentes lentos e limite de concorrência |
| Falha de um agente não derruba os demais | teste de isolamento e retry/backoff |
| Tarefas sensíveis exigem aprovação | teste negativo de autorização |
| Toda saída possui proveniência | evidências e hashes no resultado |
| Usuário consegue pausar tudo | kill switch e teste de retomada |
| Trading não ultrapassa Paper/Testnet | invariantes no domínio e teste de tentativa LIVE |
| Agência se recupera após reinício | teste de persistência e retomada |
| Operador vê estado real | snapshot, eventos, health e métricas |
| Operação contínua é controlável | start/stop/status por tenant, sem auto-start e com teste de lifecycle |

## Fora do escopo desta fase

Não serão habilitados pagamentos, publicação ampla, mensagens automáticas sem aprovação, operações financeiras reais, empréstimos, custódia de recursos, alteração de credenciais, exclusão de dados ou deploy irreversível.

## Métricas de sucesso

A primeira versão será medida por taxa de tarefas concluídas com evidência, tempo de conclusão, taxa de falhas recuperadas, quantidade de ações bloqueadas corretamente, custo por missão, taxa de aprovação humana e divergência entre estado exibido e estado persistido.

## Decisões assumidas

A agência começará como um monólito modular com workers concorrentes e armazenamento persistente, em vez de vários microserviços. Essa escolha reduz complexidade de operação e permite extrair serviços somente quando houver medição de saturação ou necessidade de isolamento.
