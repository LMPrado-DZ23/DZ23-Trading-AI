# Política de segurança

## Escopo

Esta política cobre o código, a API, o runtime multi-tenant, os adapters de execução, o painel e os artefatos de deployment deste repositório.

## Reporte responsável

Não publique vulnerabilidades não corrigidas em issues, discussões ou pull requests públicos. Abra um reporte privado pelos recursos de segurança do GitHub do repositório ou contate os mantenedores por um canal privado disponível no perfil da organização. Inclua versão/commit, impacto, passos mínimos para reprodução, logs sem secrets e uma sugestão de mitigação se possível.

Nunca envie credenciais, tokens, chaves de API, dados de conta, endereços completos ou dumps de produção no reporte.

## Prioridade

São especialmente importantes: bypass de tenant/RBAC, exposição de secrets, execução sem Risk Engine, falha do KillSwitch, divergência de reconciliação aceita como segura, injeção no painel, autenticação quebrada e qualquer caminho para dinheiro real.

## Práticas do projeto

- `LIVE_TRADING=false` e `REAL_MONEY_EXECUTION=false` por padrão.
- Segredos ficam em secret manager ou variáveis protegidas, nunca no Git.
- O frontend não envia ordens diretamente à corretora.
- Falhas de reconciliação são fail-closed.
- Mudanças de segurança exigem testes de regressão e auditoria.

## Dependências

Atualizações de dependências devem ser revisadas quanto a licenciamento, vulnerabilidades conhecidas e compatibilidade com os limites de execução segura.
