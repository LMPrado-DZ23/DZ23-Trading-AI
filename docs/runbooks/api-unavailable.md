# Runbook: DZ23ApiUnavailable

Confirme `/healthz` por HTTPS e o healthcheck do contêiner. Verifique reinícios, armazenamento, banco selecionado e os avisos retornados pelo endpoint. Não altere modo de corretora nem habilite LIVE durante o incidente. Se houver fallback de PostgreSQL para SQLite, preserve os dois armazenamentos e reconcilie antes de qualquer retomada.

Recuperação: restaure o último artefato verificado em ambiente isolado, valide integridade e só então reinicie o serviço. Encerramento exige duas coletas verdes consecutivas e registro da causa.
