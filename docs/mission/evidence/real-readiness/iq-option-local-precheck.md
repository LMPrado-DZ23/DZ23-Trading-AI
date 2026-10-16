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

## Invariantes preservadas

- handshake futuro limitado a `initialize` e `tools/list`;
- nenhuma ferramenta remota executável pelo DZ23;
- nenhuma ordem, saque, transferência ou consulta de saldo;
- `MESA_LIVE_LIBERADA=False` permanece inalterado.
