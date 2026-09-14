# Domínio dz23trading.com.br — o que cadastrar, onde e por quê

Checklist único para o domínio oficial do produto. Cada item tem o valor
EXATO a colar.

## 1. DNS (no registrador do domínio)
O domínio ainda não resolve — este é o primeiro passo.

| Tipo  | Nome | Valor                    |
|-------|------|--------------------------|
| A     | `@`  | `<IP-DO-SERVIDOR>` (IP público do servidor onde roda o Coolify) |
| CNAME | `www`| `dz23trading.com.br`     |

Propagação leva de minutos a algumas horas. Confira com:
`nslookup dz23trading.com.br`

## 2. Coolify
- Domínio da aplicação: `https://dz23trading.com.br`
- Marque a emissão automática de certificado (Let's Encrypt).
- Aponte também `www` se quiser que funcione com e sem o prefixo.

## 3. Woovi — webhook de pagamento
Cadastre exatamente:
```
https://dz23trading.com.br/v1/webhooks/woovi
```
Evento: **cobrança concluída** (`OPENPIX:CHARGE_COMPLETED`).
A chave pública do webhook vai na variável `WOOVI_WEBHOOK_PUBLIC_KEY`.

> Sem assinatura válida o sistema recusa o webhook — é o que impede alguém
> de ativar um plano de graça mandando um POST.

## 4. Google Cloud — OAuth
Credenciais → seu cliente OAuth → **URIs de redirecionamento autorizados**:
```
https://dz23trading.com.br/v1/auth/oauth/google/callback
```
E em **Origens JavaScript autorizadas**:
```
https://dz23trading.com.br
```

> ATENÇÃO: o cliente OAuth atual aponta para `api.dz23.com.br/api/oauth/...`.
> Esse endereço **não existe** neste sistema. Ou você adiciona a URI acima no
> Google, ou o login social falha com `redirect_uri_mismatch`. Adicionar é
> gratuito e não quebra o que já existe.

## 5. Azure (Microsoft) — OAuth
App registrations → seu app → Authentication → **Redirect URIs (Web)**:
```
https://dz23trading.com.br/v1/auth/oauth/microsoft/callback
```

## 6. Variáveis finais no Coolify
```
ACCESS_PASSWORD=<senha forte 20+>
MULTI_TENANT_ENABLED=true
TENANT_TOKEN_SIGNING_KEY=<openssl rand -hex 32>
SIGNUP_ENABLED=true

SUPABASE_PROJECT_REF=camhdvnrddwxwfmpvsdf
SUPABASE_DB_PASSWORD=<Supabase → Settings → Database>
SUPABASE_REGION=<região do projeto, ex.: us-east-1>

WOOVI_TOKEN=<token da API Woovi>
WOOVI_WEBHOOK_PUBLIC_KEY=<chave pública, PEM ou base64>

GOOGLE_CLIENT_ID=<...>
GOOGLE_CLIENT_SECRET=<...>
GOOGLE_REDIRECT_URI=https://dz23trading.com.br/v1/auth/oauth/google/callback
MICROSOFT_CLIENT_ID=<...>
MICROSOFT_CLIENT_SECRET=<...>
MICROSOFT_REDIRECT_URI=https://dz23trading.com.br/v1/auth/oauth/microsoft/callback

DESK_INTERVAL_SECONDS=300
```

## 7. Conferência depois do deploy
| Endereço | Esperado |
|---|---|
| `https://dz23trading.com.br/healthz` | `status: ok` e a origem do banco |
| `https://dz23trading.com.br/` | login com botão **Criar conta** |
| `https://dz23trading.com.br/v1/auth/oauth/providers` | provedores configurados |
| `https://dz23trading.com.br/floor` | Trading Floor isométrica |

Se o `healthz` responder `degraded`, ele diz exatamente o que corrigir — o
serviço sobe mesmo com configuração incompleta, de propósito.
