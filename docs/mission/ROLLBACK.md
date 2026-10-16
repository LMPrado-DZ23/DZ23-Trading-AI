# DZ23 Trading AI — rollback e recuperação

Este procedimento é para PAPER/SHADOW/TESTNET e para a futura preparação
REAL. Ele não autoriza LIVE, não fecha posições na corretora e não substitui
um plano de contingência externo.

## Princípios

- pare o processo e acione a parada de emergência antes de restaurar estado;
- preserve o diretório atual: nunca use `git reset --hard` nem apague `/data`;
- restaure primeiro em diretório novo e valide antes de trocar o volume;
- um pacote local não contém o estado guardado em PostgreSQL externo;
- rollback de código e rollback de dados são decisões separadas.

## Evidência mínima antes de uma mudança

1. registre o SHA implantado e o SHA candidato;
2. gere um backup local verificável;
3. inspecione o manifesto e guarde o SHA-256 retornado;
4. se `DATABASE_URL` estiver ativo, gere e teste também o backup lógico do
   PostgreSQL pelo mecanismo do provedor;
5. confirme `/healthz`, `/readyz` e `/v1/operational-readiness` antes da troca.

```bash
python scripts/backup_data.py backup /data /data/backups/dz23-pre-change.tar.gz
python scripts/backup_data.py inspect /data/backups/dz23-pre-change.tar.gz
```

## Recuperação de dados local

Com o serviço parado, restaure em um diretório vazio:

```bash
python scripts/backup_data.py restore /data-restaurado /data/backups/dz23-pre-change.tar.gz
python scripts/verify_local_restore.py \
  --data-dir /data --archive /data/backups/dz23-pre-change.tar.gz
```

Para registrar o gate, o arquivo ensaiado deve estar diretamente em
`DATA_DIR/backups`. A prova expira após 30 dias e é invalidada se esse arquivo
for removido ou tiver o hash alterado.

Suba uma instância isolada apontando para `/data-restaurado` e valide:

- `/healthz` responde 200;
- `/readyz` não omite o estado do kill switch;
- login do proprietário funciona;
- Escola, Sala REAL, pilotos e provas reaparecem;
- ledger, PAPER/SHADOW, journals e checkpoints estão íntegros;
- nenhuma ordem é enviada durante a validação.

Somente depois dessa prova troque o volume. Se for indispensável restaurar
sobre um destino ocupado, preserve uma cópia dele e use `--overwrite` de
forma explícita.

## Recuperação do PostgreSQL externo

Pare as escritas do DZ23 e crie pelo provedor um banco temporário vazio. Ele
deve ser distinto da origem. Exporte os DSNs apenas no ambiente da sessão e
execute:

```bash
export DATABASE_URL='postgresql://...origem...'
export POSTGRES_RESTORE_DATABASE_URL='postgresql://...banco-vazio...'
python scripts/verify_postgres_restore.py --data-dir /data
```

O comando:

- inclui somente as tabelas DZ23 conhecidas e exige `jobs` e
  `dz23_desk_state`;
- recusa origem e destino iguais e recusa qualquer tabela já existente no
  schema `public` do destino;
- usa formato custom do `pg_dump`, sem dono ou privilégios;
- não usa `--clean`, não executa `DROP` e não apaga o banco restaurado;
- mede a origem antes e depois do dump e recusa se ela mudou;
- compara hashes de esquema e de todas as linhas após o restore;
- mantém senha/DSN fora da linha de comando, logs e prova.

O resultado válido gera o dump em `/data/backups` e a prova
`/data/postgres-restore-proof.json`, ambos vinculados por SHA-256. A prova
expira em 30 dias e também fecha se a origem configurada mudar. Descarte o
banco temporário somente pelo fluxo controlado do provedor, depois de guardar
a evidência necessária.

## Rollback de código

Faça o rollback pelo mecanismo do deployment para um SHA conhecido e
registrado. Não altere a árvore de trabalho usada para investigação. Em Git,
prefira uma nova branch ou `git revert` auditável; não reescreva histórico.

Depois do rollback, confira que o SHA executado é o esperado e repita os
healthchecks. Mantenha `LIVE_TRADING=false`, `REAL_MONEY_EXECUTION=false` e o
selo `MESA_LIVE_LIBERADA=False` durante toda a recuperação.

## Critérios para abortar e manter bloqueado

- hash divergente ou manifesto incompleto;
- restauração parcial;
- PostgreSQL sem backup/restore comprovado;
- divergência de reconciliação;
- identidade de conta/corretora diferente;
- kill switch, métricas ou healthchecks indisponíveis;
- SHA implantado diferente do aprovado.

Qualquer um desses estados mantém a operação REAL bloqueada e exige nova
investigação. Não se compensa falha de recuperação abrindo LIVE.
