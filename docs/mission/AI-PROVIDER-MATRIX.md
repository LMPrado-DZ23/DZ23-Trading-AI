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
