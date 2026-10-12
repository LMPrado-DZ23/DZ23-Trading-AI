# DECISÃO DE PRODUTO — WINDOWS LOCAL FIRST (2026-10-12)

**Decisão expressa do proprietário:** DZ23 Trading AI será inicialmente executado no **PC pessoal Windows**. NÃO usar servidor/Coolify/cloud como requisito da entrega. Suporte servidor pode existir posteriormente como modo opcional.

## Critérios obrigatórios
1. Aplicativo pessoal single-owner, interface PT-BR, iniciar e encerrar pela interface/atalho Windows, sem abrir terminais automaticamente e sem depender de conta SaaS. Instalação/desinstalação previsíveis; usar empacotamento adequado ao projeto existente sem reescrever toda a stack por conveniência.
2. Python/FastAPI/CCXT e agentes existentes executados localmente por processo gerenciado pelo aplicativo, bind em 127.0.0.1 e autenticação local (loopback não substitui autenticação). Nenhuma porta pública/firewall liberada por padrão.
3. Banco local durável inicial SQLite se concorrência controlada em processo único, com WAL/backup/restore, proteção de integridade, permissões e migrações; Postgres opcional para necessidades reais de concorrência, não dependência obrigatória.
4. Integrar Ollama local como opção, com descoberta explícita dos modelos disponíveis e APIs de provedores externos opcionais. A disponibilidade de IA não deve desativar controle de risco nem bloqueios de ordens.
5. Dashboard real e responsivo; paper/shadow/testnet; conectores com integridade de feed e inventário de capabilities. IQ Option isolada como experimental. Não confundir suporte da CCXT com conexão ou negociação homologada.
6. Agente só pode operar enquanto serviço local estiver ativo e PC conectado. Suspensão, hibernação, reinício, relógio errado, perda de conexão ou encerramento da janela devem acionar política fail-closed e reconciliação antes de retomar. Nunca prometer 24x7 com PC desligado.
7. Nunca ativar LIVE, funding, alavancagem, depósitos, saques, APIs com permissão de saque ou acesso remoto sem decisão humana adicional. Capital futuro planejado R$ 5.000 não autoriza negociação.
8. Entrega-alvo: instalador Windows assinado quando certificado autorizado estiver disponível; sem certificado, artefato instalável identificado como NÃO ASSINADO, com SHA-256, instruções e teste em Windows 10/11. Não fingir homologação Windows feita apenas em Linux CI.
9. Testar instalador limpo, atualização sem perda de dados, desinstalação preservando dados por padrão, relançamento após crash, network down, CPU/RAM, ciclos sleep/wake, múltiplas instâncias, proteção contra ordens duplicadas, kill switch e UX com Playwright.
10. Preservar código do PR #7 e demais correções do núcleo financeiro; integrar de modo rastreável, sem apagar histórico.
11. Atualizar documentos de missão, gates, árvore e instruções de distribuição para refletir WINDOWS LOCAL FIRST. Docker/Coolify são futuros opcionais. Não efetuar deploy no servidor nesta missão.

## Status real
Este arquivo é requisito de arquitetura, NÃO implementação nem comprovação de testes. Criar checkpoint específico e inventariar o Windows alvo antes de build/distribuição real. LIVE bloqueado.
