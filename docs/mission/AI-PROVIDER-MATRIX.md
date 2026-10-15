# DZ23 Trading AI — EXTERNAL AI PROVIDER MATRIX

| Provider | Base URL | Model Selection | Authentication | Fallback Supported | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **OpenAI** | `https://api.openai.com/v1` | GPT-4o, GPT-4o-mini | API Key | Yes | PASS |
| **Anthropic** | `https://api.anthropic.com` | Claude 3.5 Sonnet, Haiku | API Key | Yes | PASS |
| **Google Gemini** | `https://generativelanguage.googleapis.com` | Gemini 1.5 Pro, Flash | API Key | Yes | PASS |
| **DeepSeek** | `https://api.deepseek.com` | DeepSeek Chat, Coder | API Key | Yes | PASS |
| **Groq** | `https://api.groq.com/openai/v1` | Llama 3.3 70B, Mixtral | API Key | Yes | PASS |
| **Mistral AI** | `https://api.mistral.ai/v1` | Mistral Large, Small | API Key | Yes | PASS |
| **OpenRouter** | `https://openrouter.ai/api/v1` | Universal Aggregator | API Key | Yes | PASS |

*Note: Ollama / local GPUs are not required for v1. External commercial API keys are configured strictly by the owner via Settings > AI.*

---

**Correção datada (2026-10-15, MISSÃO V9/F5):** os sete "PASS" acima eram alegação de configuração, não homologação contra serviço real. A classificação vinculante por nível de prova está em [evidence/v9-baseline/f5-classificacao-conectores.md](evidence/v9-baseline/f5-classificacao-conectores.md). Resultado real na sondagem do caminho do produto (`testar_provedor`, GET /models, zero tokens): **DeepSeek, Groq, Mistral, OpenRouter e Ollama local homologados** (HTTP 200 com listas de modelos; DeepSeek também com inferência real no teste live do repo); **OpenAI, Anthropic e Google com estado `sem_chave` fail-closed** (nenhuma saída de rede — sem chave no ambiente; o caminho do catálogo lê `GOOGLE_API_KEY`, e a `GEMINI_API_KEY` presente no ambiente do dono não é lida por esse caminho até a chave ser salva pela configuração do painel); **outro OpenAI-compatível com `sem_url`** (exige endereço informado).
