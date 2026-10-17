# IQ Option MCP — precheck local sanitizado

Data: 2026-10-16

## Escopo

Foram inspecionados localmente os arquivos fornecidos pelo proprietário:

- `claude_desktop_config (3).json`;
- `mcp (1).json`.

Nenhum valor de credencial foi impresso, copiado para o repositório ou enviado
ao registro de precheck. Nenhum endpoint MCP foi conectado nesta etapa.

## Resultado local

- 7 servidores em cada arquivo, 14 entradas no total;
- endpoints HTTPS para `binary-options`, `blitz-options`, `digital-options`,
  `marginal-cfd`, `marginal-crypto`, `marginal-forex` e `turbo-options`;
- todas as entradas usam cabeçalho `Authorization: Bearer` preenchido;
- as 14 entradas referenciam o mesmo valor opaco;
- o valor não é JWT, portanto a expiração não pode ser inferida offline;
- o contrato coincide com `service/iq_option_mcp.py`, que aceita o token apenas
  em memória/ambiente e não expõe `call_tool`.

## PolicyLayer

O comando `npx -y policylayer precheck <identificador> --json --rules` foi
executado para os sete identificadores. Todos retornaram `status=unknown`:
não há registro publicado e os nomes não resolvem para pacotes npm com formato
MCP. As consultas foram registradas pelo serviço como demanda não atendida.

Esse resultado não classifica os servidores como seguros ou perigosos. Ele
significa apenas que o registro não oferece um parecer. Conforme a política de
precheck, o handshake autenticado aguarda decisão explícita do proprietário de
prosseguir sem registro ou aguardar a publicação de um registro.

O HEAD também contém `scripts/verify_iq_option_mcp.py`, uma CLI fail-closed que
recebe a credencial exclusivamente pela variável
`IQ_OPTION_MCP_BEARER_TOKEN`, executa a sonda já restrita a `initialize` e
`tools/list` e salva apenas evidência sanitizada. A CLI não aceita token como
argumento e também evita ecoar valores de argumentos inválidos.

## Handshake passivo autorizado

Após autorização explícita do proprietário para prosseguir mesmo com o estado
`unknown` do registro, o precheck foi repetido e os sete identificadores
permaneceram `unknown`. Em seguida, a CLI fail-closed foi executada contra os
sete endpoints oficiais em 2026-10-16, no HEAD
`9a49c0fde3d15b9017a032cb8a7ef638d07a9644`.

Resultado sanitizado: `PASS`, 7/7 conexões `MCP_METADATA_CONNECTED`, somente
`initialize` e `tools/list`, 92 ferramentas descritas pelos servidores e zero
ferramentas chamáveis pelo DZ23 (`callable_tool_count=0`). A credencial ficou
somente no ambiente do processo e foi removida ao final. Nenhuma ferramenta,
consulta de conta ou ordem foi executada.

O contrato foi depois fixado por servidor e revalidado contra os mesmos sete
endpoints: 60 ferramentas somente leitura reconhecidas, 32 ferramentas
mutáveis classificadas e bloqueadas, zero desconhecidas, zero ausentes e zero
chamáveis. O verificador agora retorna `MCP_CONTRACT_DRIFT` e falha fechado se
um nome for adicionado, removido, duplicado ou vier vazio. Essa classificação
não concede permissão de chamada; ela apenas impede que mudança remota seja
confundida com integração validada.

O catálogo passa a informar `OFFICIAL_MCP_METADATA_VALIDATED`. Isso valida
somente a disponibilidade do contrato MCP passivo; não equivale a credencial
de leitura da conta, sandbox homologado ou autorização de execução.

## Escopo oficial do token

O painel autenticado oficial `profile/ai-integration` foi inspecionado em
2026-10-16 sem revelar, copiar, criar, reemitir ou excluir credenciais. A tela
confirma `Read access` obrigatório e controles separados de `Trading access`
para Options e Margin Products. O token DZ23 existente estava com esses
acessos de negociação habilitados, em conformidade com as 32 ferramentas
mutáveis observadas no catálogo passivo, e portanto não satisfaz o gate de
permissão mínima.

O código passa a reconhecer os dois catálogos oficiais, mas só permite a prova
de conta quando todos os sete endpoints anunciam o catálogo estritamente
somente leitura. Se `tools/list` expuser qualquer ferramenta mutável, a prova é
recusada antes de `call_tool`. Nenhuma consulta de conta foi executada nesta
validação.

## Invariantes preservadas

- handshake futuro limitado a `initialize` e `tools/list`;
- nenhuma ferramenta remota executável pelo DZ23;
- nenhuma ordem, saque, transferência ou consulta de saldo;
- `MESA_LIVE_LIBERADA=False` permanece inalterado.

## Token estritamente read-only criado e comprovado

Em 2026-10-17, após confirmação explícita no momento da ação, foi criado no
painel oficial o token `DZ23 Read Only` com `Read access` ligado e bloqueado
pelo provedor e todos os nove controles de `Trading access` desligados. A
lista passou de dois para três tokens ativos; os dois tokens existentes não
foram alterados nem excluídos. O prazo padrão de um ano foi preservado.

O token foi armazenado cifrado na configuração local do DZ23 e a sonda
passiva foi executada no HEAD
`99025cb45dc08aa984194e1b17d2cd735900ec17`. Resultado sanitizado:

- `PASS`, 7/7 endpoints em `MCP_METADATA_CONNECTED`;
- 60 ferramentas somente leitura, zero mutáveis;
- zero ferramentas desconhecidas, ausentes ou chamáveis;
- `metadata_only=true`, `tool_execution_enabled=false`;
- `orders_enabled=false` e `ordens_enviadas=false`.

Essa prova substitui o diagnóstico do token antigo com 32 ferramentas
mutáveis e fecha o gate de permissão mínima do token. Naquele momento ela
ainda não havia executado `list_balances`, lido a conta ou confirmado sua
identidade; a etapa seguinte está registrada abaixo.

## Conta regular autenticada sem persistir identidade bruta

Em 2026-10-17, o DZ23 executou a chamada literal somente leitura
`list_balances({types: NORMAL})` com o token `DZ23 Read Only`. O provedor
devolveu exatamente uma conta `regular`, com um único identificador não vazio.
Esse identificador foi comparado somente em memória por uma segunda chamada
do mesmo método, sob nova validação do contrato `tools/list`.

Resultado sanitizado:

- leitura autenticada e identidade da conta confirmadas;
- rótulo local `IQ Option Real Principal`;
- uma única conta regular e um único método de leitura utilizado;
- zero ferramenta mutável exposta ao DZ23;
- token mantido cifrado no armazenamento local;
- nenhum saldo, moeda, token, `balance_id` ou identificador pessoal impresso;
- nenhum identificador bruto persistido;
- nenhuma ordem, saque, transferência ou alteração de conta executada.

A prova local foi validada pelo próprio gate de prontidão e vale por 24 horas.
Ela fecha os itens "Acesso REAL de leitura recente e permissões mínimas" e
"Identidade da conta REAL confirmada". Não cria credencial de execução, não
homologa TESTNET da IQ Option e não abre o selo LIVE.
