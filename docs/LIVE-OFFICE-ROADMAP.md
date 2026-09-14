# Escritório ao vivo — entregas e pendências

## Publicado neste ciclo

- Claw3D definido como referência arquitetural principal.
- OpenClaw Office definido como referência complementar de UX isométrica e streaming.
- Licenças MIT verificadas e requisitos de atribuição documentados.
- DZ23 preservado como única fonte de verdade.
- `GET /v1/office/snapshot` criado para consolidar agência, Paper, Shadow, KillSwitch, jobs e cursores de eventos.
- `GET /v1/office/events` criado para combinar eventos incrementais de agência e execução sem inferência visual.
- Contratos multi-tenant e Live bloqueado preservados.

## Ainda falta implementar

1. Substituir a cena simples de `service/static/floor.html` por um escritório isométrico responsivo.
2. Renderizar desks de Research, Strategy, Risk, Portfolio, Execution e Monitoring.
3. Vincular estados dos agentes e animações exclusivamente a eventos oficiais.
4. Criar Committee Room, War Room, Risk Room, broker health, order blotter, posições e PnL.
5. Implementar reconexão, heartbeat e replay incremental na interface.
6. Adicionar testes dos contratos do escritório e smoke visual/funcional.
7. Atualizar o Docker local e validar o escritório com o backend multi-tenant real.
8. Preservar avisos MIT no `NOTICE` se qualquer código substancial ou ativo das referências for incorporado.

## Critério de conclusão

A interface não pode inventar ordens, fills, posições, PnL, votos ou status. Cada alteração operacional deve corresponder a um snapshot oficial ou evento versionado do backend DZ23.
