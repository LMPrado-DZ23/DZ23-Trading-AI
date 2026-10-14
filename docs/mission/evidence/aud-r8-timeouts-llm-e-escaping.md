# Rodada-8 — Auditoria pós-selo: timeouts HTTP do caminho LLM desabilitados + escapamento do painel (tasks #108–#111)

**Auditoria estática + comportamental de 2026-10-14** na branch
`fix/instalador-atualizacoes-windows` (pós-selo `f41e150`). Dois achados
REAIS — investigados até a causa-raiz, corrigidos com RED→GREEN e pinados
por teste. Nada foi ocultado por `skip` nem por assert afrouxado.

## Achado A (crítico) — todos os timeouts HTTP do caminho LLM ficam DESABILITADOS por requisição

**Sintoma:** uma conexão travada com um provedor de IA pendura a chamada
sem limite algum — o ciclo da mesa pode ficar preso para sempre esperando
`complete()`, porque o timeout *efetivo* por requisição é `None`.

**Inventário varrido (caminho por caminho, não por suposição):**

| # | Caminho (arquivo) | Veredito | Mecanismo comprovado |
|---|---|---|---|
| a | openai-family: `openai_client.py` — cobre openai, xai, qwen, qwen-cn, glm, glm-cn, openrouter, mistral, kimi, groq, nvidia, ollama, deepseek, minimax, local-compatible | **DEFEITO COMPROVADO** | langchain-openai 1.6.6 passa `"timeout": self.request_timeout` (=None default) EXPLICITAMENTE ao `openai.OpenAI(...)`; o SDK 3.19.2 trata None explícito como valor "dado" (`is_given(None)` é True), pula o bloco que adotaria o timeout do `http_client` fornecido e carimba `self.timeout=None` em TODA requisição (`_build_request(timeout=self.timeout)` → extensões httpx `{connect:None, read:None, write:None, pool:None}`) |
| b | `azure_client.py` | **DEFEITO COMPROVADO** | mesma construção da langchain-openai (herda o mesmo caminho do SDK) |
| c | `anthropic_client.py` | **DEFEITO COMPROVADO** | `ChatAnthropic.default_request_timeout` (alias `timeout`) = None → `_client.timeout = None`; o SDK Anthropic também carimba o timeout por requisição |
| d | `google_client.py` | **DEFEITO COMPROVADO** | `self.timeout` None → `http_options.timeout None`; o campo é consumido POR REQUISIÇÃO (`int(self.timeout * 1000)` ms em `chat_models.py` do langchain-google-genai 4.4.0) |
| e | `bedrock_client.py` | **NÃO PROVÁVEL NESTE AMBIENTE** | `langchain_aws` ausente (extra opcional) — postura não declarada; ver limitações |

**Prova comportamental (pré-fix, via sonda de introspecção + probe de
enforcement):** com todos os `timeout` = None (postura real até esta
rodada), uma resposta travada NÃO dispara timeout; com um valor finito
(float ou `httpx.Timeout`) a `ReadTimeout` é imposta. Ou seja: o default
de 5 s do `httpx.Client` endurecido era peso morto — sobrescrito por
requisição pelo None carimbado.

**Correção (2 camadas, com precedência do usuário preservada):**

1. `tradingagents/llm_clients/transport.py` — nova fonte única
   `LLM_HTTP_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)`
   (finito e generoso; `read` cobre completações longas). Os DOIS builders
   endurecidos (sync/async) passam `timeout=LLM_HTTP_TIMEOUT` — declaração
   autoritativa, e o caminho `not_given` do SDK a adota para usuários
   diretos do transporte.
2. `setdefault("timeout", ...)` explícito em cada cliente —
   `openai_client.py` e `azure_client.py` recebem o `httpx.Timeout`;
   `anthropic_client.py` e `google_client.py` recebem **float em segundos**
   (`float(LLM_HTTP_TIMEOUT.read)` = 300.0), porque o pydantic da
   ChatAnthropic rejeita `httpx.Timeout` (ValidationError comprovado) e o
   modelo Google converte o float para ms em cada chamada. O passthrough
   do usuário (chave `timeout` em `self.kwargs`) roda DEPOIS e prevalece.

## Achado B — dados do servidor crus em `innerHTML` no painel (2 desvios)

A convenção do próprio arquivo (`escapeHtml`, `service/static/index.html`)
manda todo dado de servidor passar por escapamento; duas renderizações
desviavam:

1. `operacaoReal` (`carregarEstadoOperacional`) — `op.estado` e `op.motivo`
   inseridos crus.
2. `renderEvents` — `event_type`, `event.aggregate_id` e `sequence` crus.

**Correção:** `escapeHtml(...)` nos dois pontos; **pinos estáticos** em
`tests/test_painel_render_escaping.py` (3 testes: helper continua definido;
`operacaoReal` escapa ≥2 campos; `renderEvents` escapa ≥3 campos).

## Prova determinística (RED→GREEN)

| Achado | RED capturado | GREEN |
|---|---|---|
| openai sync/async + azure | sonda pré-fix: SDK carimba `timeout=None` (todos os 4 intervalos None); testes novos falhavam contra `ESPERADO=(10,300,30,10)` | `tests/test_llm_transport_timeouts.py` verde |
| anthropic | `assert None == 300.0` (`_client.timeout`; 2 failed, 2 passed in 3.98s — os 2 que passaram eram os de precedência do usuário) | `_client.timeout == 300.0` e usuário `timeout=5.0` → 5.0 |
| google | `assert None == 300.0` (`llm.timeout`) | `llm.timeout == 300.0` e usuário `timeout=7.5` → 7.5 |
| escapamento do painel | pinos estáticos falhavam contra o HTML pré-fix | verde |

## Provas frescas (2026-10-14)

| Evidência | Resultado |
|---|---|
| RED anthropic/google capturado em execução | `2 failed, 2 passed in 3.98s` |
| `tests/test_llm_transport_timeouts.py` (10 testes) | **10 passed** (6,83 s) — include precedência do usuário nos 3 clientes |
| Vizinhança de provedores (`test_anthropic_effort`, `test_google_api_key`, `test_google_thinking_level`, `test_provider_registry`) | **43 passed** (2,41 s) — nenhum contrato vizinho quebrado |
| Suíte completa na árvore final | **3015 passed / 6 skipped / 71 subtests** (exit 0, 296,34 s = baseline 3002 + 13 novos: 6+4 timeouts + 3 escapamento) |
| E2E Playwright na árvore final | **26 passed in 41.78s** |
| Ruff (`llm_clients/`, `tests/test_llm_transport_timeouts.py`, `tests/test_painel_render_escaping.py`) | All checks passed |
| PNGs de evidência e2e (churn de datas de execução) | restaurados para o HEAD antes do commit — um commit de código não carrega ruído de execução |

## Limitações declaradas (honestidade)

1. **Bedrock não provável neste ambiente** (`langchain_aws` ausente — extra
   opcional; skip já declarado no relatório B §4). A postura de timeout do
   caminho Bedrock NÃO é declarada aqui — exigirá rodada própria quando o
   extra estiver instalado.
2. **Nenhuma chamada real a provedor de IA** foi feita (sem credenciais
   autorizadas nesta sessão; nenhuma chave fictícia): as provas são de
   construção/introspecção dos clientes reais + probe de enforcement do
   httpx local. O caminho Google converte o campo para ms por requisição —
   comprovado por LEITURA do código do `langchain-google-genai` 4.4.0
   (`int(timeout * 1000)`), não por request HTTP real.
3. **`timeout` do usuário prevalece** (decisão de desenho): quem passar
   `timeout=None` explícito mantém a postura antiga — silenciosamente
   consciente, não acidental.
4. **Reabertura do ciclo de aceite:** esta é mudança de PRODUTO (4
   clientes + painel) — o congelamento do selo `f41e150` fica revogado para
   efeito de citação; a re-homologação local está feita (tabela acima), e
   CI do push + re-citação do artefato de aceite foram registrados na
   sequência desta rodada (mesmo padrão das tasks #105/#107).

## Arquivos

- `tradingagents/llm_clients/transport.py` (fonte única `LLM_HTTP_TIMEOUT`; builders sync/async)
- `tradingagents/llm_clients/openai_client.py` (setdefault — cobre os 15 caminhos openai-compatible)
- `tradingagents/llm_clients/azure_client.py` (setdefault)
- `tradingagents/llm_clients/anthropic_client.py` (setdefault float 300 s)
- `tradingagents/llm_clients/google_client.py` (setdefault float 300 s; convertido a ms por requisição)
- `service/static/index.html` (escapamento em `operacaoReal` e `renderEvents`)
- `tests/test_llm_transport_timeouts.py` (10 testes — RED→GREEN)
- `tests/test_painel_render_escaping.py` (3 testes estáticos — RED→GREEN)
