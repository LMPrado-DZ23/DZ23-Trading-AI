# Política de segurança

## Escopo

Esta política cobre o código, a API, o runtime **single-owner** (um dono por
instalação), os adapters de execução, o painel e os artefatos de deployment
(Windows e servidor Linux) deste repositório.

Recursos de SaaS/multi-tenant **não existem** neste produto e não podem ser
reativados por configuração — a fronteira responde 404 por construção
(fail-closed), inclusive com flags de ambiente ligadas de propósito.

## Reporte responsável

Não publique vulnerabilidades não corrigidas em issues, discussões ou pull requests públicos. Abra um reporte privado pelos recursos de segurança do GitHub do repositório ou contate os mantenedores por um canal privado disponível no perfil da organização. Inclua versão/commit, impacto, passos mínimos para reprodução, logs sem secrets e uma sugestão de mitigação se possível.

Nunca envie credenciais, tokens, chaves de API, dados de conta, endereços completos ou dumps de produção no reporte.

## Prioridade

São especialmente importantes: bypass de tenant/RBAC, exposição de secrets, execução sem Risk Engine, falha do KillSwitch, divergência de reconciliação aceita como segura, injeção no painel, autenticação quebrada e qualquer caminho para dinheiro real.

## Práticas do projeto

- `LIVE_TRADING=false` e `REAL_MONEY_EXECUTION=false` por padrão.
- Senha do dono guardada como hash **Argon2id**; código de recuperação de
  uso único (o antigo morre a cada troca). Nunca em texto puro.
- Segredos ficam em secret manager ou variáveis protegidas, nunca no Git.
  O CI varre o repositório (gitleaks) e gera SBOM a cada mudança.
- O frontend não envia ordens diretamente à corretora.
- Falhas de reconciliação são fail-closed.
- Mudanças de segurança exigem testes de regressão e auditoria.

## Dependências

Atualizações de dependências devem ser revisadas quanto a licenciamento, vulnerabilidades conhecidas e compatibilidade com os limites de execução segura.
