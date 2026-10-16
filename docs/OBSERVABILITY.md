# Observabilidade operacional

Este contrato responde a três perguntas de plantão:

1. A API usada pelo dono está disponível e rápida?
2. Os controles financeiros estão bloqueando com segurança quando necessário?
3. A própria telemetria está saudável, sem séries descartadas?

Ele não autoriza LIVE. A observabilidade é um pré-requisito operacional; a liberação de dinheiro real continua dependendo do checklist autenticado, das provas externas, da confirmação do dono no momento da ação e de `MESA_LIVE_LIBERADA=False` enquanto o selo não for auditado.

## Superfícies

- `GET /healthz`: liveness pública do processo e do armazenamento selecionado.
- `GET /readyz`: prontidão pública e estado da parada de emergência.
- `GET /metrics`: snapshot autenticado `dz23.metrics/2`.
- `X-Request-ID`: UUID canônico. Valor ausente ou inválido é substituído por UUID; o mesmo ID volta na resposta e no log estruturado. Texto livre e tokens não são aceitos como ID.

Os logs HTTP contêm apenas ID, método, template da rota, status e duração. Não incluem URL concreta, query string, headers, IP, corpo, conta, ativo ou credenciais.

## Métricas RED

`http.requests` é um contador rotulado por `method`, template `route` e `status_class`. `http.request.duration_ms` é um histograma com os mesmos rótulos e limites fixos. O snapshot publica contagem, soma, máximo e limites superiores aproximados de p50, p95 e p99.

Somente templates de rota entram nos rótulos; caminhos desconhecidos viram `unmatched`. O registro aceita no máximo 512 séries rotuladas e incrementa `telemetry.series_dropped` se o limite for atingido. O coletor deve calcular taxas e janelas por delta entre snapshots, pois os valores reiniciam junto com o processo.

## SLO e alertas

Objetivos iniciais, a revisar após 30 dias de carga real sem ordens LIVE:

- disponibilidade da API: 99,9% em 30 dias, medida por `/healthz`;
- taxa de 5xx: menor que 1% em qualquer janela de 5 minutos com ao menos 20 requisições;
- latência p95: até 2 segundos em janela de 5 minutos com ao menos 20 requisições;
- parada de emergência: qualquer estado armado deve ser visível em até 1 minuto;
- séries descartadas: zero.

As regras canônicas estão em `ops/alerts.yml`. O adaptador do provedor de monitoramento deve preservar IDs, limiares, duração e links de runbook. Uma coleta única ou um teste local não constitui prova de SLO; a evidência válida precisa vir do monitor externo durante uma janela contínua.

## Implantação segura

1. Guarde o bearer de `/metrics` no secret store do monitor, nunca em argumento de linha de comando, arquivo versionado ou rótulo.
2. Colete `/healthz`, `/readyz` e `/metrics` a cada 30 segundos sobre HTTPS.
3. Converta snapshots cumulativos em deltas antes de avaliar taxa de erro e latência da janela.
4. Faça um teste sintético de cada alerta e anexe a entrega recebida à evidência de prontidão.
5. Se a coleta falhar, mantenha LIVE bloqueado; ausência de telemetria não significa saúde.

## Política de cardinalidade

Permitido: método HTTP, template de rota e classe de status. Proibido: request ID como rótulo, URL concreta, query string, usuário, tenant, conta de corretora, ativo, order ID, token, segredo ou texto de erro arbitrário.
