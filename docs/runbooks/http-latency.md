# Runbook: DZ23HttpLatencyHigh

Identifique o template com p95 acima de 2 segundos e compare CPU, memória, banco e dependências externas. Não aumente timeouts como primeira resposta. Rotas financeiras lentas podem produzir estado incerto; mantenha a parada de emergência armada até reconciliar.

Feche o incidente somente após duas janelas abaixo do limite, sem crescimento de 5xx e com o estado financeiro reconciliado.
