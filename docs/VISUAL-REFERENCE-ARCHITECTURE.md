# Referências visuais: Claw3D e OpenClaw Office

## Fontes auditadas

- Claw3D: https://github.com/iamlukethedev/Claw3D
- OpenClaw Office: https://github.com/wickedapp/openclaw-office

Ambos os repositórios incluem licença MIT. Se código ou ativos substanciais forem reutilizados, os respectivos avisos de copyright e permissão precisam ser preservados no NOTICE e na distribuição.

## Decisão arquitetural

O Claw3D será a referência principal para limites arquiteturais: a interface visual é um cliente/proxy e o runtime DZ23 permanece a fonte de verdade. A própria arquitetura do Claw3D declara que agentes, sessões e execução pertencem ao gateway; a cena deriva animações dos eventos do runtime em vez de manter estado operacional paralelo.

O OpenClaw Office será referência complementar para layout isométrico, posicionamento de agentes, indicadores de tarefa, timeline e estratégia SSE compartilhada. Seu componente `IsometricOffice` gera posições adaptativas e anima transferências de tarefa. Seu hook `useWorkflowStream` usa uma conexão SSE compartilhada, heartbeat de 30 segundos, reconexão e eventos tipados.

## Regras da integração DZ23

A implementação não copiará o runtime OpenClaw nem criará uma segunda fonte de verdade. O DZ23 continuará autoritativo para dados, agentes, decisões, risco, ordens, fills, posições, PnL, auditoria e reconciliação. A cena consumirá `/v1/agency/snapshot`, `/v1/agency/events` e `/v1/agency/stream` com a sessão multi-tenant. Animações somente refletirão eventos oficiais. Não serão inferidos fills, posições ou PnL a partir de textos, timers ou movimento visual.

A primeira versão será implementada no frontend nativo já servido pelo FastAPI para evitar duas stacks concorrentes. Ela adotará profundidade isométrica CSS e eventos reais. Uma migração futura para Three.js poderá usar Claw3D como base desde que a autenticação, o proxy server-side, a licença MIT e o isolamento multi-tenant sejam preservados.
