# Runbook: DZ23TelemetrySeriesDropped

Inspecione mudanças recentes nos rótulos e confirme que somente método, template de rota e classe de status são usados. Nunca aumente o limite para acomodar URL, conta, ativo, ID ou texto arbitrário. Corrija o emissor de alta cardinalidade e reinicie apenas em janela segura, sabendo que métricas em memória serão zeradas.

Encerramento exige zero novos descartes durante duas janelas e revisão de que nenhum dado sensível entrou em logs ou métricas.
