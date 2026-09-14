# INSTALAR — DZ23 Trading Agency

Guia único para colocar o sistema no ar e começar a vender. ~15 minutos.

## 1. Banco de dados (Supabase, recomendado)
1. Abra seu projeto no dashboard do Supabase.
2. **Settings → Database → Database password** — copie (ou redefina) a senha.
3. Guarde também o **project ref** (aparece na URL do dashboard).

Não precisa criar tabela nenhuma: o sistema cria o que precisa ao subir.

## 2. Deploy (Coolify)
**New Resource → Dockerfile**, repositório `tradingagents-dz23`, branch
`unificacao`. Volume: **`/data`**. Variáveis:

```
ACCESS_PASSWORD=<senha forte de 20+ caracteres>
MULTI_TENANT_ENABLED=true
TENANT_TOKEN_SIGNING_KEY=<32+ caracteres aleatórios: openssl rand -hex 32>
SIGNUP_ENABLED=true

SUPABASE_PROJECT_REF=<ref do projeto>
SUPABASE_DB_PASSWORD=<senha do banco>

WOOVI_TOKEN=<token da API Woovi>
WOOVI_WEBHOOK_PUBLIC_KEY=<chave pública do webhook, formato PEM ou base64>

DESK_INTERVAL_SECONDS=300
```

Opcional (login social):
```
GOOGLE_CLIENT_ID= / GOOGLE_CLIENT_SECRET= / GOOGLE_REDIRECT_URI=https://SEU-DOMINIO/v1/auth/oauth/google/callback
MICROSOFT_CLIENT_ID= / MICROSOFT_CLIENT_SECRET= / MICROSOFT_REDIRECT_URI=https://SEU-DOMINIO/v1/auth/oauth/microsoft/callback
```
> As URLs de redirecionamento precisam ser cadastradas **iguais** no Google
> Cloud e no Azure, senão o provedor recusa o login.

Deploy. Se algo estiver errado, o serviço **sobe mesmo assim** e explica o
problema em `/healthz` e no painel — ele nunca morre calado.

## 3. Webhook de pagamento (Woovi)
No painel da Woovi, cadastre:

```
https://SEU-DOMINIO/v1/webhooks/woovi
```

Evento: cobrança concluída. O sistema **só** ativa plano com webhook
assinado — corpo adulterado ou assinatura ausente são recusados.

## 4. Conferir se subiu
- `https://SEU-DOMINIO/healthz` → `status: ok`, `banco: Supabase ...`
- `https://SEU-DOMINIO/` → tela de login com o botão **Criar conta**
- Logado como admin: **Administração → banco** (`/v1/admin/database`)

## 5. Primeira venda
1. O cliente abre o site e clica **Criar conta** → 7 dias grátis.
2. Configura a IA (Groq é grátis) e roda a primeira análise.
3. Em **Plano e pagamento**, escolhe Starter (R$ 99) ou Pro (R$ 249) e paga
   por PIX (QR Code ou copia-e-cola).
4. A Woovi confirma → o plano ativa sozinho. Assinatura vencida bloqueia a
   mesa, mas **nunca** a página de pagamento.

## 6. Operação
- **RUNBOOK.md** — incidentes, backup e restauração.
- **COMECE-AQUI.md** — guia do usuário final.
- Backup: aba Mesa de Agentes → **Gerar backup** (vai para `/data/backups`).

## Limites honestos
- A mesa opera em **PAPER** e testnet. Dinheiro real está desligado por
  invariante da plataforma (`LIVE_TRADING=false`).
- Resultado de teste não é promessa de lucro; nada aqui é recomendação de
  investimento.
