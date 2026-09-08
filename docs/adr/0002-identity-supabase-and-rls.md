# ADR 0002 — Identidade, Supabase Auth e isolamento PostgreSQL

## Status

Aceito no marco M1 em 2026-09-08. Ainda sujeito às auditorias finais M10.

## Decisão

- A aplicação mantém uma única sessão opaca própria, vinculada a usuário,
  organização, membership e expiração. Tokens Supabase só atravessam a rota de
  troca no login e nunca são persistidos.
- Google, Apple e demais provedores são autenticados pelo Supabase. A API
  verifica assinatura assimétrica contra o JWKS do projeto fixado, valida
  issuer/audience/expiração e confirma a mesma identidade no endpoint oficial
  `/auth/v1/user`. `user_metadata` não é autoridade de verificação.
- A sessão social não dura além do access token apresentado. O login posterior
  exige uma nova troca; a sessão local continua sendo a fronteira do produto.
- E-mail não é uma chave global. Contas não verificadas de uma organização não
  reservam o endereço contra uma identidade social verificada em outra. Nunca
  há vinculação automática apenas por coincidência de e-mail.
- PostgreSQL usa a role dedicada `tradingagents_app`, criada sem superuser,
  `BYPASSRLS`, criação de banco ou criação de roles. O runtime confirma a role
  e seus atributos em toda conexão. DSN administrativa é recusada.
- Todas as tabelas tenant-aware usam `ENABLE` e `FORCE ROW LEVEL SECURITY`.
  `app.tenant_id` é definido localmente à transação. O catálogo versionado de
  termos é global e somente leitura para o runtime.
- MFA/TOTP é criptografado em repouso. O step-up é persistido na sessão exata e
  expira em cinco minutos; uma futura operação capital-facing deve consultar
  `assert_recent_step_up`, não confiar em resposta do navegador.
- Bloqueio de conta é persistente e independente do IP, combinado com rate
  limit distribuído no Redis por identidade e por origem.

## Operação Supabase

1. Aplicar as migrations Alembic com uma conexão administrativa efêmera.
2. Definir uma senha aleatória para `tradingagents_app` por canal operacional
   seguro. A senha não entra em migration, Git, logs ou chat.
3. Configurar no secret store do servidor a DSN que autentica diretamente como
   `tradingagents_app`, com `sslmode=verify-full` ou, no mínimo, `require`.
4. Habilitar Google/Apple no painel Supabase somente depois de cadastrar os
   redirect URIs do domínio de produção e guardar os client secrets no Supabase.
5. Apple exige rotação operacional do client secret. A validade e o alerta de
   rotação devem fazer parte do runbook M8/M9.

## Consequências

O `service_role` do Supabase não é necessário no browser nem no runtime desta
fronteira. Uma chave desse tipo já exposta fora do secret store deve ser
revogada. O publishable key pode estar no frontend, mas é fixado ao projeto e
não concede autoridade administrativa.
