# FQ-0 — Sondagens de factibilidade das referências externas (§4/§6/§10)

**Missão:** "DZ23 Trading AI — Evolução Quantitativa: Freqtrade + FreqAI + OpenBB (integração seletiva e validada)".
**Data:** 2026-10-14 · **Branch:** `fix/instalador-atualizacoes-windows` · **Regra da sondagem:** apenas METADADOS públicos (PyPI JSON API) — nenhuma instalação no venv do projeto; nenhum código GPL incorporado ao núcleo; nenhuma execução financeira.

## 1. Ambiente da estação

| Item | Valor | Comando |
| :--- | :--- | :--- |
| Python do venv do projeto | **3.12.10** | `.venv/Scripts/python.exe --version` |

## 2. freqtrade — metadados oficiais do PyPI

Fonte: `https://pypi.org/pypi/freqtrade/json`

| Campo | Valor verificado |
| :--- | :--- |
| Versão publicada | **2026.9** |
| `requires_python` | **>=3.11** → compatível com o Python 3.12 da estação (instalável) |
| Dependências declaradas | **85** (pacote pesado ⇒ venv/processo ISOLADO é o único padrão aceitável para uso como ferramenta) |
| Licença (metadado) | **GPLv3** — `License :: OSI Approved :: GNU General Public License v3 (GPLv3)` |

**Confronto com a posição já registrada no repositório** (`docs/REPOSITORIOS-VALOR-E-INTEGRACAO-DZ23.md` L22/L65): "Freqtrade: GPLv3; preferir referência ou processo separado" — **coerente**: a licença GPLv3 permite usar como ferramenta separada; não autoriza copiar código para o núcleo (Apache-2.0) sem análise jurídica.

## 3. openbb — metadados oficiais do PyPI

Fonte: `https://pypi.org/pypi/openbb/json`

| Campo | Valor verificado |
| :--- | :--- |
| Versão publicada | **5.0.0** |
| `requires_python` | **<4, >=3.10** → compatível com o Python 3.12 |
| Dependências declaradas | **36** (pacote-base; provedores específicos podem somar dependências) |
| Licença | **Apache-2.0** — interoperável com o uso pretendido (adaptador independente de dados; **não é corretora**, não executa ordens) |

### 3.1 Cobertura de provedores OpenBB V5 (documentação oficial; sondagem read-only)

Fonte: documentação oficial `docs.openbb.co` (V5, atualizada 2026-09-29; cópias brutas em `.research/ob_providers.txt` e `.research/ob_faq_providers.txt`, não versionadas).

| Fato verificado | Implicação para o §6 |
| :--- | :--- |
| 17 pacotes de provedor na V5, cada um com namespace próprio (`obb.fred`, `obb.sec`, `obb.cboe`…); **OpenBB não hospeda dados** — conecta a serviços públicos/terceiros cujos termos, cobertura e limites de taxa são definidos pela fonte. | O adaptador (se construído) deve registrar **provedor + termos + frequência** por requisição; nunca prometer cobertura. |
| Somente 2 exigem chave: **FRED** (macro EUA; chave gratuita) e **EIA** (energia). BLS/CFTC/Congress têm credenciais **opcionais**. Todos os demais são **sem chave**. | Viável usar provedores gratuitos sem custo; nenhum dado pago sem autorização do dono (§6). |
| Ações EUA: **Cboe** (delayed) e **Nasdaq** (website público) — keyless. Macro: FRED, ECB, IMF, OECD, BLS, Treasury. Cripto: **Deribit** (público). | Cobre o caso "contexto para estratégias de prazo maior" **dos EUA**; **não** há prova de cobertura Brasil. |
| Provedores V4 **removidos na V5**: yfinance, fmp, intrinio, benzinga, tiingo, tradier, tradingeconomics, wsj, seeking-alpha, finviz, etc. (últimas releases exigem openbb-core 1.x — **incompatíveis** com V5). | Qualquer código/exemplo antigo baseado em `obb.equity`/`obb.economy` (routers compartilhados V4) **não funciona** na V5 — o adaptador deve mirar namespaces de provedor. |
| Cobertura não-EUA limitada a **Canadá (TMX)** e **Nasdaq Nordic/Baltic**. | Confirma o §6: "Não afirmar cobertura brasileira ou americana completa sem testar os provedores específicos" — a auditoria registra **ausência de B3/Brasil** na V5. |

## 3.2 Spike de execução do freqtrade em processo ISOLADO (§4) — EXECUTADO

Objetivo: comprovar factibilidade do §4 ("Freqtrade como motor independente de validação, em processo isolado e exclusivamente sem execução financeira") **sem tocar o venv do projeto**.

| Item | Valor |
| :--- | :--- |
| Local do spike | `C:\Users\DZ23\Documents\Qoder\2026-10-12\2f7f19a4\freqtrade-spike\` — **FORA do repositório** |
| Isolamento | venv próprio (`freqtrade-spike/.venv`), criado com `uv venv` (CPython 3.12.10) |
| Instalação | `uv pip install freqtrade` → **exit 0** (85 dependências resolvidas; nenhuma tocada no venv do projeto) |
| Prova de execução (verbatim) | `freqtrade import OK, versao: 2026.9` — `Operating System: Windows-11-10.0.26200-SP0` · `Python Version: Python 3.12.10` · `CCXT Version: 4.5.88` · `Freqtrade Version: freqtrade 2026.9` |
| Escopo do spike | Técnico apenas: **nenhuma conta configurada, nenhuma credencial, nenhuma execução financeira**, nada escrito no `%APPDATA%` do produto nem tocado na instalação do dono (`:8000`). |

**Conclusão do spike:** o freqtrade 2026.9 é **instalável e executável como ferramenta separada** na estação do dono (Windows 11 / Python 3.12.10). Isso satisfaz a factibilidade do §4 **sem** incorporar código GPL ao núcleo e **sem** qualquer risco financeiro. A promoção a "motor de validação" ainda exige o desenho de equivalência documentada (§4) — registrado como decisão na matriz FQ.

## 4. Comandos reproduzíveis (verbatim)

```bash
# Python do venv do projeto
.venv/Scripts/python.exe --version   # Python 3.12.10

# freqtrade (metadados)
curl -s https://pypi.org/pypi/freqtrade/json | .venv/Scripts/python.exe -c "import json,sys; d=json.load(sys.stdin); i=d['info']; print(i['version'], i['requires_python'], len(i.get('requires_dist') or []), i.get('license'))"
# 2026.9 >=3.11 85 GPLv3

# openbb (metadados)
curl -s https://pypi.org/pypi/openbb/json | .venv/Scripts/python.exe -c "import json,sys; d=json.load(sys.stdin); i=d['info']; print(i['version'], i['requires_python'], len(i.get('requires_dist') or []), i.get('license'))"
# 5.0.0 <4,>=3.10 36 Apache-2.0

# freqtrade — spike de execução em venv ISOLADO (fora do repositório; §3.2)
# cd C:\Users\DZ23\Documents\Qoder\2026-10-12\2f7f19a4\freqtrade-spike
# uv venv && uv pip install freqtrade        # exit 0
# .venv/Scripts/python.exe -c "import freqtrade; print('freqtrade import OK, versao:', freqtrade.__version__)"
# .venv/Scripts/freqtrade.exe --version      # freqtrade 2026.9 / Windows-11 / Python 3.12.10 / CCXT 4.5.88
```

## 5. Limitações declaradas

- Sondagem de **metadados** (versão/licença/compatibilidade/contagem de dependências) **e** spike de execução do freqtrade em venv ISOLADO (§3.2 — import + `--version`, **sem** conta, **sem** credencial, **sem** execução financeira e **sem** backtest nesta rodada; nenhum código GPL copiado para o repositório).
- A cobertura de provedores OpenBB (§3.1) foi levantada da **documentação oficial** (cópia bruta em `.research/`, não versionada) — **nenhum pacote OpenBB foi instalado** nesta rodada e **nenhum** provedor foi chamado ao vivo; a verificação "ao vivo" de um provedor específico, se necessária ao adaptador, será testada em rodada própria com estado de disponibilidade honesto.
- Contagem de dependências do `openbb` refere-se ao pacote-base; provedores de dados específicos (gratuitos/pagos) têm pacotes próprios e termos próprios — a auditoria de provedores (§6) depende da documentação oficial e será registrada na matriz FQ.
- Nenhum dado pago foi acessado; nenhuma credencial usada; `%APPDATA%` e a instalação do dono (`:8000`) intocados.
