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

O catálogo passa a informar `OFFICIAL_MCP_METADATA_VALIDATED`. Isso valida
somente a disponibilidade do contrato MCP passivo; não equivale a credencial
de leitura da conta, sandbox homologado ou autorização de execução.

## Invariantes preservadas

- handshake futuro limitado a `initialize` e `tools/list`;
- nenhuma ferramenta remota executável pelo DZ23;
- nenhuma ordem, saque, transferência ou consulta de saldo;
- `MESA_LIVE_LIBERADA=False` permanece inalterado.
