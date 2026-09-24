# Auditoria técnica — DZ23 Trading Agency

**Repositório:** `LMPrado-DZ23/tradingagents-dz23`
**Commit auditado:** `527757a` (`main`)
**Data:** 24 de setembro de 2026
**Escopo:** estrutura, execução local, testes, lint, CI, API, autenticação multi-tenant, endpoints de LLM, execução de ordens, configuração e dependências.

## Resumo executivo

O projeto apresenta uma base acima da média para um sistema experimental de trading: há separação explícita entre paper/testnet e live, `ExecutionRouter` com bloqueio por risco e reconciliação, idempotência, kill switch, isolamento por tenant, testes negativos de segurança e uma suíte extensa. A avaliação geral é **boa para MVP controlado**, mas ainda não recomendaria declarar o sistema pronto para produção financeira sem corrigir o modo de degradação multi-tenant, endurecer a confiança em proxies, tornar as dependências reprodutíveis e melhorar a observabilidade de falhas.

Durante a auditoria foi encontrado e corrigido um defeito real no cliente LLM: o provider genérico `openai_compatible` podia herdar `OPENAI_BASE_URL` ou `OPENAI_API_BASE` do ambiente global, mesmo exigindo um endpoint próprio. Isso permitia que um endpoint ambientado fosse usado de forma inesperada pelo relay genérico. A correção restringe a leitura dessas variáveis ao provider oficial `openai` e adiciona teste de regressão.

## Validação executada

| Verificação | Resultado |
|---|---:|
| `python -m compileall -q tradingagents service scripts cli` | passou |
| `ruff check .` | passou |
| `python scripts/smoke_multitenant.py` | passou |
| Suíte limpa: `env -u OPENAI_BASE_URL -u OPENAI_API_BASE pytest -q` | **1.279 passaram, 2 ignorados** |
| Testes direcionados após correção | **90 passaram** |
| `git diff --check` | passou |

Os dois testes ignorados dependem de integração opcional ou credencial externa: Bedrock sem `langchain_aws` e chamada live do DeepSeek sem chave.

A primeira execução sem limpar o ambiente apresentou 10 falhas porque o sandbox forneceu simultaneamente `OPENAI_BASE_URL` e `OPENAI_API_BASE`. Isso revelou o bug do provider genérico; após a correção e a execução sem essas variáveis ambientadas, a suíte completa passou.

## Correção aplicada

### [Alta] Isolamento do endpoint do provider `openai_compatible`

**Arquivo:** `tradingagents/llm_clients/openai_client.py`
**Teste:** `tests/test_openai_compatible_provider.py`

Antes, a resolução de endpoint consultava `OPENAI_BASE_URL` e `OPENAI_API_BASE` para qualquer provider OpenAI-compatible quando não havia URL explícita. Assim, o provider genérico podia aceitar uma URL do ambiente oficial do SDK, embora a própria API exigisse `base_url` explícita e usasse uma credencial separada (`OPENAI_COMPATIBLE_API_KEY`).

A lógica agora só consulta as variáveis de endpoint do SDK quando `self.provider == "openai"`. Para `openai_compatible`, a ausência de URL continua falhando com `ValueError`, independentemente do ambiente global.

**Status:** corrigido e validado com teste de regressão, lint e compilação.

## Achados prioritários ainda pendentes

### P1 — Fail-open na inicialização do modo multi-tenant

Em `service/app.py`, quando `MULTI_TENANT_ENABLED=true` mas o store de identidade não pode ser inicializado, o código altera `MULTI_TENANT_ENABLED` para `False` e mantém o serviço ativo. Se `ACCESS_PASSWORD` estiver configurada, o processo passa ao modo legado de senha única.

Isso é operacionalmente conveniente, mas perigoso para um produto multi-tenant: uma falha de configuração ou indisponibilidade do banco pode remover o caminho de autenticação por tenant e mudar a superfície de isolamento. O comportamento recomendado para produção é **fail-closed**: retornar `503`/estado degradado e impedir operações autenticadas até que o store de identidade esteja disponível. Se a equipe quiser manter a página de diagnóstico, ela deve ficar limitada a `/healthz`, `/readyz` e uma tela sem dados protegidos.

**Correção sugerida:** separar `MULTI_TENANT_CONFIGURED` de `MULTI_TENANT_ENABLED`; não cair automaticamente para o modo legado quando o modo multi-tenant foi explicitamente solicitado. Adicionar teste que simule falha de conexão e confirme que não é possível acessar dados de tenant com a senha legada.

### P1 — Confiança irrestrita em `X-Forwarded-For`

`_client_ip()` usa o primeiro valor de `X-Forwarded-For` diretamente. Em uma exposição direta à internet, o cliente pode forjar esse cabeçalho e contornar o rate limit de autenticação, criando um IP novo a cada tentativa. Em uma instalação atrás de proxy, a confiança deveria depender de uma lista explícita de proxies confiáveis.

**Correção sugerida:** por padrão usar `request.client.host`; só interpretar `X-Forwarded-For` quando `TRUSTED_PROXY_NETWORKS` estiver configurado e o peer imediato pertencer a uma rede confiável. Preferir rate limit distribuído por conta/token além de IP, pois o dicionário `_FAILS` é local ao processo.

### P1 — Dependências não reprodutíveis

`pyproject.toml` declara dezenas de dependências com limites mínimos (`>=`) e o repositório não contém lockfile Python. O CI instala o estado mais recente disponível em cada execução. Isso aumenta o risco de uma atualização transitiva quebrar segurança, compatibilidade ou comportamento de trading sem alteração no código.

**Correção sugerida:** adotar lockfile gerado por ferramenta escolhida pela equipe (`uv.lock`, Poetry ou equivalente), atualizar de forma controlada e manter uma rotina de atualização semanal com testes. Separar dependências de runtime, desk e desenvolvimento em arquivos/artefatos reprodutíveis.

### P2 — Auditoria de dependências não está no CI

O CI cobre testes, compilação, lint, instalação limpa e smoke tests, mas não executa auditoria de vulnerabilidades. O `pip-audit` executado nesta máquina encontrou vulnerabilidades em pacotes instalados globalmente, incluindo `pip`, `pypdf`, `weasyprint`, `wheel` e `xhtml2pdf`. Esses resultados não devem ser atribuídos automaticamente ao projeto, pois parte deles não aparece nas dependências diretas do `pyproject.toml`; ainda assim, mostram que o pipeline precisa ter um gate explícito e um ambiente isolado.

**Correção sugerida:** executar `pip-audit` no ambiente/lockfile do projeto, documentar exceções, atualizar a imagem base e falhar o CI em vulnerabilidades exploráveis de runtime. Também atualizar `pip`/`wheel` durante a construção da imagem.

### P2 — Falhas de alertas são silenciosamente descartadas

Em `service/app.py`, o callback `_record()` captura `Exception` e executa `pass` quando o envio de alertas falha. Isso impede que o ciclo de trading seja derrubado, o que é desejável, mas também elimina a evidência operacional de que o proprietário deixou de receber um alerta.

**Correção sugerida:** registrar falha sanitizada em log estruturado e métrica (`alerts.delivery_failed`), manter estado de retry/backoff e exibir o último erro não sensível no painel. O alerta não deve derrubar o ciclo, mas a falha também não pode desaparecer.

### P2 — Configuração não usa escrita atômica

`save_cfg()` grava diretamente com `Path.write_text()`. Uma interrupção durante a escrita pode deixar `config.json` truncado. O projeto já possui testes e mecanismos de escrita atômica em outras áreas, portanto a configuração deveria seguir o mesmo padrão.

**Correção sugerida:** escrever em arquivo temporário no mesmo diretório, aplicar `chmod 600`, fazer `flush`/`fsync`, substituir com `os.replace()` e, quando aplicável, manter uma cópia anterior válida. Adicionar teste de interrupção simulada e recuperação.

### P2 — Estado de sessão e rate limit são locais ao processo

Sessões são persistidas no store, mas o rate limit de senha legado (`_FAILS`) e vários estados operacionais (`_LIVE_HALTED`, `_DESKS`, `_cancel`) ficam em memória. Em múltiplos workers ou após reinício, o comportamento pode divergir: rate limit é perdido e comandos de parada/estado podem não ser vistos por outro processo.

**Correção sugerida:** declarar oficialmente que o serviço exige um único worker até haver coordenação externa, ou mover estado crítico para Postgres/Redis com operações atômicas. O kill switch e a parada de emergência, em particular, devem ter uma fonte persistente e compartilhada.

## Pontos positivos

- O padrão de operação é paper/testnet e o caminho live possui várias travas independentes.
- O `ExecutionRouter` verifica kill switch, reconciliação, correspondência completa entre pedido e decisão, risco e idempotência antes de enviar ao adapter.
- Há testes específicos para isolamento de tenant, segredos, transporte, endpoints, rate limit, reconciliação, execução e estados de falha.
- O adapter CCXT falha fechado para modo inválido, exchange fora da allowlist, ausência de limites live e ausência de testnet.
- A resposta de configuração mascara chaves e o código evita devolver segredos inteiros pela API.
- O smoke multi-tenant cobre bootstrap, login, criação de usuário, isolamento, sessão e logout.
- O CI usa matriz de Python, instalação limpa, lint e smoke checks, além de `dependabot`.

## Melhorias de médio prazo

1. Criar contratos de erro únicos para a API, com `code`, mensagem segura, correlação e documentação OpenAPI protegida ou publicada separadamente.
2. Adicionar testes de autorização negativa por cada rota sensível, especialmente `admin`, `risk`, `kill_switch`, importação/exportação e operações live.
3. Persistir eventos de emergência e mudança de configuração com ator, tenant, timestamp, motivo e hash de integridade.
4. Adicionar timeout, limite de tamanho e validação de schema para importação/exportação; atualmente o pacote contém segredos por desenho e merece uma confirmação operacional mais explícita.
5. Executar testes com Python 3.10–3.13 em ambiente realmente limpo e adicionar um job que detecte variáveis de endpoint ambientadas indevidamente.
6. Adicionar métricas e alertas para latência de exchange, reconciliação pendente, falhas de persistência, falhas de notificação e rejeições do risk engine.
7. Reduzir a dependência de `os.environ` global em `run_job`; passar configuração e clientes por contexto explícito evita vazamento acidental e limita a serialização global por `_lock`.

## Veredito

**Request changes antes de produção multi-tenant/financeira.**

Para uso local, pesquisa e paper/testnet controlado, o projeto está funcional e bem protegido em vários caminhos importantes. A correção do endpoint genérico foi aplicada e validada. Antes de exposição comercial ou execução com dados/contas reais, recomendo priorizar o fail-closed multi-tenant, a confiança em proxy, a persistência do estado crítico e a reprodutibilidade/auditoria das dependências.

## Arquivos modificados nesta auditoria

- `tradingagents/llm_clients/openai_client.py`
- `tests/test_openai_compatible_provider.py`

Nenhum commit ou push foi realizado; as mudanças estão apenas na cópia local clonada para auditoria.



## Implementação da rodada seguinte

As recomendações foram aplicadas localmente, sem push ou merge:

- O modo multi-tenant solicitado agora bloqueia rotas autenticadas quando sua inicialização falha, em vez de cair silenciosamente para a senha legada.
- `X-Forwarded-For` só é aceito para rate limit quando o peer está em `TRUSTED_PROXY_NETWORKS`; sem isso, usa-se o IP direto da conexão.
- O estado de parada de emergência live passou a ser persistido em `/data/live-halts.json` com escrita atômica e recarga entre workers.
- Falhas de entrega de alertas agora geram métricas e logs sanitizados sem interromper o ciclo da mesa.
- `save_cfg()` passou a usar o escritor JSON atômico já existente.
- Foi criado `uv.lock`; o CI usa `uv sync --locked` nos jobs principais.
- O CI ganhou job de `pip-audit`, além de atualização de `pip`/`wheel` na instalação limpa e na imagem Docker.
- `.env.example` e README documentam a instalação travada e a configuração de proxy confiável.
- Foram adicionados testes de regressão para fail-closed, proxy confiável, persistência de parada e isolamento do endpoint LLM genérico.

### Gates finais da implementação

- `uv lock --check`: passou.
- `ruff check .`: passou.
- `python -m compileall -q tradingagents service scripts cli`: passou.
- `uv run pytest -q`: **1.283 passaram, 2 ignorados, 71 subtestes**.
- Smoke API e multi-tenant: passaram.
- `pip-audit --local --progress-spinner off`: **No known vulnerabilities found**.
- `git diff --check`: passou.

Os testes ignorados continuam sendo Bedrock sem `langchain_aws` e a chamada live do DeepSeek sem credencial, ambos explicitamente opcionais.
