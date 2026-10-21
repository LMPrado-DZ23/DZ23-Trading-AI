# MCP da mesa DZ23 para o DS Bot

Este servidor expõe a API local da mesa como ferramentas MCP **read-only**,
com um único comando de escrita: `run_paper_cycle`, que só roda em PAPER e
com opt-in explícito. Não existe ferramenta de ordem, de configuração ou de
dinheiro real.

## Por que MCP (e não plugin)

- A mesa já tem HTTP/JSON; o MCP só traduz para ferramentas tipadas.
- O bot não recebe chave de corretora nem shell livre.
- O mesmo servidor serve DS Bot, Claude Code, Codex e qualquer cliente MCP.
- O motor continua sendo o Python; o bot é a camada de operação/supervisão.

## Instalação

```bash
uv sync --extra desk --extra mcp
```

O servidor é `mcp_server.py`, na raiz do repositório. Ele usa `httpx` contra
`DZ23_API_URL` (padrão `http://127.0.0.1:8080`).

## Variáveis de ambiente

| Variável | Padrão | Para que serve |
|---|---|---|
| `DZ23_API_URL` | `http://127.0.0.1:8080` | Endereço da API da mesa |
| `DZ23_ACCESS_PASSWORD` | — (obrigatória) | Token Bearer da API; nunca é devolvido |
| `DZ23_MCP_ENABLE_CYCLE` | `0` | `1` habilita `run_paper_cycle` (somente PAPER) |
| `DZ23_HTTP_TIMEOUT` | `30` | Timeout das chamadas HTTP, em segundos |
| `DZ23_MCP_LOG_LEVEL` | `INFO` | Nível de log (stderr) |

## Ferramentas

| Ferramenta | Escreve? | O que devolve |
|---|---|---|
| `platform_status` | Não | Capacidades públicas, LIVE bloqueado |
| `health` | Não | `/healthz` + `/readyz` |
| `desk_status` | Não | Modo, agentes, ordens, posições, ciclos |
| `risk_summary` | Não | Reservado/disponível, contabilidade, pendências |
| `open_positions` | Não | Posições e guardião |
| `recent_cycles(limit)` | Não | Últimos ciclos (1..20) |
| `live_checklist` | Não | O que falta para dinheiro real |
| `traders` | Não | Estratégias, estágios, último motivo |
| `run_paper_cycle` | **Sim, só PAPER** | Dispara um ciclo; recusa LIVE |

## DS Bot (stdio)

Em **Settings → Capabilities → MCP server**, use:

```
transport: stdio
command:   C:\caminho\do\repo\.venv\Scripts\python.exe
args:      C:\caminho\do\repo\mcp_server.py
env:       DZ23_API_URL=http://127.0.0.1:8080
           DZ23_ACCESS_PASSWORD=<senha da API>
           DZ23_MCP_ENABLE_CYCLE=0
```

Monte essa capability **somente no bot Operador**. Analista e Risco ficam sem
`run_paper_cycle` (read-only). O DS Bot não deve receber a chave da corretora
em nenhuma hipótese.

### Prompt de operação (exemplo)

> Leia `risk_summary` e `recent_cycles(3)`. Se houver pendência de
> reconciliação, veto ou drawdown anormal, me avise com o motivo exato do
> relatório. Não proponha ordem: a mesa decide. Se eu pedir um ciclo, use
> `run_paper_cycle` apenas com a mesa em PAPER.

## Limites e avisos

- `run_paper_cycle` não é um botão de trade: ele só chama o ciclo PAPER já
  existente; a mesa pode decidir não operar.
- O MCP não substitui o veto de risco nem o selo LIVE.
- Se a API estiver fora do ar, as ferramentas devolvem `{"ok": false, ...}`;
  o bot deve dizer isso, não inventar estado.
- O processo MCP não deve ser exposto na rede; ele fala com a API local.
- O DS Bot só executa enquanto o DSH estiver aberto; para 24/7, o motor roda
  como serviço e o bot apenas supervisiona quando conectado.

## Testes

```bash
uv run pytest tests/test_mcp_server.py -q
```

Os testes usam `httpx.MockTransport` e cobrem: auth obrigatória, erro HTTP
estruturado, ciclo desabilitado, recusa fora de PAPER e o caminho PAPER.
