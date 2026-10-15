# V9-F8.2 — Secret scanning (gitleaks): execuções, triagem e risco

Data: 2026-10-15 (UTC). Executor: Qoder. Ferramenta: gitleaks v8.30.1 (`ghcr.io/gitleaks/gitleaks:latest`), todas as execuções com `--redact` (valores capturados não são reproduzidos nos artefatos).

**Conclusão antecipada: nenhuma credencial real em nenhuma execução.** Os 17 achados das varreduras da árvore (execuções #2–#8) estão classificados abaixo em 4 famílias (a)–(d), todas justificadas com evidência; as varreduras finais da árvore congelada (#9 e #10) apontam os mesmos 17 mais 10 digests sha256 do manifesto F8 (família (e), mesma classe já triada); o histórico (52) repete as mesmas famílias.

## 1. Execuções e artefatos

| # | Modo | Alvo | Artefatos (sha256 do arquivo) | Achados |
|---|------|------|-------------------------------|---------|
| 1 | `detect --source=/repo --log-opts=--all` | histórico completo do checkout principal (commitado) | [v9-gitleaks-historico.log](v9-gitleaks-historico.log) `19117eaa…`; [report](v9-gitleaks-historico-report.json) `89551075…` | 52 |
| 2 | `dir /repo` + config | árvore ATUAL do worktree `dz23-v9-audit` (inclui alterações não commitadas) | [v9-gitleaks-dir.log](v9-gitleaks-dir.log) `245e03fe…`; [report](v9-gitleaks-dir-report.json) `b9520002…`; [config](v9-gitleaks-dir.toml) `cce86a25…` | 17 |
| 3 | `dir /repo` + config (confirmação pós-edições) | mesma árvore após as edições documentais desta triagem (triagem/matriz/baseline) | [v9-gitleaks-dir-confirmacao.log](v9-gitleaks-dir-confirmacao.log) `aa142108…`; [report](v9-gitleaks-dir-confirmacao-report.json) `0362225a…` | 17 (fingerprints idênticos ao #2, diff vazio) |
| 4 | `dir /repo` + config (confirmação 2 — árvore final, já com os registros das execuções anteriores) | mesma árvore após as linhas #1–#3 desta tabela e o parágrafo de confirmação no baseline | [v9-gitleaks-dir-confirmacao2.log](v9-gitleaks-dir-confirmacao2.log) `64b1d5c0…`; [report](v9-gitleaks-dir-confirmacao2-report.json) `b9520002…` | 17 (fingerprints idênticos; report byte a byte igual ao do #2) |
| 5 | `dir /repo` + config (confirmação 3 — árvore final pós-F8.1) | mesma árvore APÓS o E2E final (log/XML + 43 capturas em `v9-e2e-final/`) e as edições documentais de F8.1 na matriz/baseline | [v9-gitleaks-dir-confirmacao3.log](v9-gitleaks-dir-confirmacao3.log) `a576b3a5…`; [report](v9-gitleaks-dir-confirmacao3-report.json) `f535f2e7…` | 17 (conjunto de fingerprints idêntico; as 17 entradas do report são byte a byte iguais às do #2 — sha256 canônico `3582b2af…`; o report difere do #2 apenas na ORDEM de serialização das entradas) |
| 6 | `dir /repo` + config (confirmação 4 — árvore com os registros da confirmação 3 — log/relatório em `evidence/v9-baseline/` — e as linhas da triagem que a citam) | mesma árvore com os artefatos do #5 e as linhas da triagem que registram o #5 | [v9-gitleaks-dir-confirmacao4.log](v9-gitleaks-dir-confirmacao4.log) `c6141b88…`; [report](v9-gitleaks-dir-confirmacao4-report.json) `b9520002…` | 17 (fingerprints idênticos; report completo byte a byte igual ao do #2 — `cmp` sem diferenças; sha canônico das 17 entradas ordenadas por fingerprint `3582b2af…`) |
| 7 | `dir /repo` + config (confirmação 5 — árvore com os registros completos das confirmações 3 e 4 — logs/relatórios em `evidence/v9-baseline/` — e as linhas da triagem/baseline que os registram) | mesma árvore com os artefatos do #6 e as linhas da triagem/baseline que registram o #5 e o #6 | [v9-gitleaks-dir-confirmacao5.log](v9-gitleaks-dir-confirmacao5.log) `169d1b84…`; [report](v9-gitleaks-dir-confirmacao5-report.json) `3b12780d…` | 17 (fingerprints idênticos; as 17 entradas do report são byte a byte iguais às do #2 — sha256 canônico `3582b2af…`; o report completo difere do #2 apenas na ORDEM de serialização das entradas — mesmo tamanho, 9650 bytes) |
| 8 | `dir /repo` + config (confirmação 6 — árvore final congelada do fechamento: com os registros completos das confirmações 3–5 — logs/relatórios em `evidence/v9-baseline/` — as linhas #1–#7 desta tabela, as citações das confirmações 2–5 na baseline e a linha F8.2 da matriz atualizada) | mesma árvore com os artefatos do #7 e as linhas da triagem/baseline que registram o #6 e o #7 | [v9-gitleaks-dir-confirmacao6.log](v9-gitleaks-dir-confirmacao6.log) `b48eb3f3…`; [report](v9-gitleaks-dir-confirmacao6-report.json) `19293b9a…` | 17 (fingerprints idênticos; as 17 entradas do report são byte a byte iguais às do #2 — sha256 canônico `3582b2af…`; o report completo difere do #2 e do #7 apenas na ORDEM de serialização das entradas — mesmo tamanho, 9650 bytes; primeira divergência de ordem vs #2 no índice 9) |
| 9 | `dir /repo` + config (confirmação 7 — árvore pós-checkpoint `11ca677`, HEAD `85af9ca`, com os commits PWA-03…07/DOC-02, as alterações não commitadas do fechamento SEC-03/TEST-01/02 e as evidências já geradas do ciclo) | mesma árvore do fechamento em curso: checkpoint com o manifesto F8, novos pinos/arquivos de teste e logs RED/GREEN/etc. em `evidence/v9-baseline/` | [v9-gitleaks-dir-confirmacao7.log](v9-gitleaks-dir-confirmacao7.log) `d1eb9d6f…`; [report](v9-gitleaks-dir-confirmacao7-report.json) `4e9e0045…` | 27 = 17 canônicos (as mesmas 15 fixtures — `test_security_fase0_r5.py` realocada de `:132` para `:136` pelo fix Playwright/asyncio do checkpoint, mesmo valor de teste — e os 2 falsos positivos estruturais do `.env.enterprise.example`) + 10 digests sha256 do manifesto `f8-integridade.json` (família (e)); varreu 30.16 MB em 8.02s; **nenhum achado em arquivo criado/editado pelas correções V9** |
| 10 | `dir /repo` + config (confirmação 8 — árvore do fechamento completa: inclui a suíte completa final, o `uv lock --check` e a varredura de segredos em `docs/` gerados após a #9) | mesma árvore da #9 acrescida de `v9-fullsuite-final.*`, `v9-meta01-uvlockcheck.log` e `v9-sweep-segredos-docs-pos-sec03.log` | [v9-gitleaks-dir-confirmacao8.log](v9-gitleaks-dir-confirmacao8.log) `b1f1e179…`; [report](v9-gitleaks-dir-confirmacao8-report.json) `c72bc1cc…` | 27 — conjunto de fingerprints **idêntico** ao da #9 (diff vazio em ambos os sentidos; os artefatos novos não introduzem nenhum achado); varreu 31.75 MB em 7.41s |

Config do modo `dir` ([v9-gitleaks-dir.toml](v9-gitleaks-dir.toml)): regras padrão (`useDefault = true`) + exclusão apenas de diretórios não-código (`.git/`, `.venv/`, `node_modules/`, `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`). Todo o restante da árvore é varrido.

Método: `detect` NÃO é válido em worktree ligado — o arquivo `.git` do worktree aponta para um gitdir absoluto inacessível dentro do contêiner e a execução devolve "0 commits scanned". A tentativa está registrada em `v9-gitleaks-worktree-tentativa-invalida.*`; NÃO é evidência (nomeada assim para não ser citada por engano). Por isso o histórico foi varrido no checkout principal e a árvore atual no modo `dir`. A primeira varredura `dir` apontou 19 itens: os 17 abaixo + 1 bytecode em `__pycache__/` (gerado, não versionado; excluído por config) + 1 falso positivo de SHA no item (d) (eliminado por reformulação — ver abaixo).

## 2. Classificação dos achados da árvore atual (17 nas rodadas #2–#8; 27 nas rodadas finais #9–#10, acrescidas da família (e))

Máscaras no formato `ab…yz(n=NN)` = prefixo/sufixo com o tamanho original; nenhum valor reproduzido na íntegra, por decisão deliberada (nem mesmo os de teste).

### (a) Fixtures de teste deliberadamente falsos — 15 de 17 (todos em `tests/`, já commitados em main)

| Local (árvore atual) | Captura mascarada | Por que é comprovadamente falso |
|---|---|---|
| `tests/test_billing_woovi.py:127` | `Q2xpZ…8=(n=24)` | base64 decodifica em placeholder textual terminando em "…SEGREDO…" |
| `tests/test_llm_transport_timeouts.py:57/:78/:126` | `sk-te…89(n=17)`, `azke…89(n=15)`, `gkey-…89(n=19)` | valores de teste com sufixo de teste; injetados via `mock.patch.dict` para exercitar timeouts de transporte |
| `tests/test_security_fase0_r5.py:132` | `sk-te…89(n=17)` | idem (timeout de transporte) |
| `tests/test_security_fase0_wiring.py:130` | `relay…23(n=19)` | chave de relé de teste |
| `tests/test_security_fase0_r2.py:23`, `r3.py:19` | `senti…YZ(n=21/23)` | literal de senha-sentinela de teste de vazamento |
| `tests/test_security_fase0_r11.py:144` | `ENVSE…YZ(n=15)` | literal de teste de precedência de variável de ambiente |
| `tests/test_security_fase0_r7.py:43/:51/:65/:75` | `sk-x-…89(n=14)`, `azke…89(n=15)` | fixtures da mesma família |
| `tests/test_security_fase0_r8.py:21` | `azke…89(n=15)` | idem |
| `tests/test_security_fase0_r8.py:182` | campos de token/password mascarados (`tok…56`, `hunt…r2`) | "hunter2" é a senha de brinquedo canônica de testes |

### (b) Placeholders públicos em arquivos de exemplo — 2 de 17

- `.env.enterprise.example:31` e `:35` — **falso positivo estrutural da regra `generic-api-key`**: as duas linhas têm valor VAZIO (`OPENAI_API_KEY=` e `GROQ_API_KEY=`; conferido diretamente no arquivo — todas as linhas 31–36 estão sem valor). A regra atravessa o CRLF (o separador admite espaços em branco) e captura o NOME da variável da LINHA SEGUINTE como pseudo-valor (ex.: `ANTHRO…`). Reprodução determinística: arquivo sintético com essas mesmas linhas `…_API_KEY=` em CRLF gera exatamente os MESMOS 2 achados (linhas 1 e 5 do sintético ↔ 31 e 35 do real), com o campo de segredo apontando o nome da variável seguinte. É comportamento da expressão regular, não conteúdo exposto.
- Histórico (mesma família, revisões antigas): `.env.example:5` (`ACCESS…(n=43)`: instrução "troque-esta-senha"), `.env.example:14` e `DEPLOY.md:14` (`ACCESS…(n=41)`: mesma instrução). Revisões atuais: as chaves de IA de `.env.example` estão comentadas e sem valor (linhas 11–19 conferidas).

### (c) Artefato gerado

- `tests/__pycache__/…pyc` — bytecode gerado localmente (não versionado). Excluído por config na varredura final.

### (d) Falsos positivos de SHA hexadecimal (mesma regra + proximidade com palavra-chave)

- `docs/mission/V9-BASELINE.md:9` — **corrigido nesta triagem**: a linha dizia "…por fetch e API GitHub: `<sha de 40 hex>`" — a adjacência palavra-chave + ":" + SHA disparava a regra `generic-api-key`. Reformulada ("…por consulta à API do GitHub (`<sha>`, …)"); a varredura final já não a aponta.
- `docs/mission/TRADING-MISSION-STATE.md` (revisão do commit `62ad0ce6`) — "Commit of API integration: `<sha>`": mesmo mecanismo, no histórico. A revisão ATUAL do arquivo não aciona a regra (foi reescrita depois; cita inclusive o incidente gitleaks histórico de fixture corrigido "sem allowlist" no commit `8e369f1`). Nada a alterar.

### (e) Digests sha256 do manifesto de integridade F8 — 10 de 27 nas varreduras finais (#9/#10)

- `docs/mission/evidence/v9-baseline/f8-integridade.json:976,977,978,981,982,984,985,998,999,1000` — o manifesto F8 registra pares caminho→sha256; as 10 linhas apontadas citam caminhos de evidência (`auth-red-*`, `ci-secrets-*`, `f5-probe-ia-auth*`) cuja adjacência palavra-chave + valor hexadecimal dispara `generic-api-key` sobre o próprio digest. Não há credencial: é o digest de integridade do artefato, classe já triada em V9-CI-03; os 10 fingerprints exatos estão pinados em `.gitleaksignore` (prefixo do commit `11ca67738adf…`) para o scan de histórico do CI. O manifesto foi gravado em disco às 11:28 UTC de 2026-10-15 e commitado no checkpoint `11ca677` (12:20 UTC) — depois da confirmação 6 (10:09 UTC, 0 achados de F8) e antes da confirmação 7.

### Histórico (52 achados)

Mesmas famílias: fixtures de teste (incluindo `tests/test_security_fase0_*` em revisões antigas — commits `48bf8c8`/`5e82e30`, `tests/test_platform_*`, `test_bootstrap_fallback.py`, `test_alertas_transparencia.py`), placeholders de exemplo (`.env.example`, `DEPLOY.md`) e 1 falso positivo de SHA (item (d)). Também são fixtures os payloads de `tests/test_platform_jobs.py` que exercitam a REJEIÇÃO de credenciais, e o vetor público RFC 4226 (TOTP de teste) de `tests/test_platform_compliance_crypto.py`.

## 3. Redaction e não-exposição

- Todas as execuções usam `--redact`; relatórios e logs exibem os valores como `REDACTED`.
- Verificação independente nos artefatos gerados: busca pelos valores crus dos fixtures conhecidos → 0 ocorrências em logs e relatórios.
- Esta triagem também é mascarada (`ab…yz(n=NN)`), para não introduzir novos padrões detectáveis no repositório.

## 4. Risco avaliado (CI e auditoria)

- O job de CI `secrets, licenças e SBOM` (gitleaks-action) roda sobre o RANGE do push/PR, não o histórico inteiro. Verificado nas execuções recentes do GitHub Actions do repositório: **verde** no push de `2b7754a` (run `38660769209`; os jobs vermelhos daquele run são `tests (py3.10–3.13)`, defeito corrigido nesta branch) e verde no run do Dependabot `38660974762` (falha apenas em `tests`). Ou seja: os fixtures históricos NÃO derrubam o CI por estarem fora do range.
- Medidas para o push da V9 não introduzir padrão detectável no range: (i) artefatos com `--redact`; (ii) reformulação do `V9-BASELINE.md:9` (item (d)); (iii) esta triagem mascarada; (iv) oito varreduras `dir` de confirmação após as edições — **#3** (após (ii)/(iii), matriz e baseline), **#4** (árvore final, já incluindo os registros das execuções anteriores), **#5** (árvore final pós-F8.1: após o E2E final — log/XML + 43 capturas — e as edições documentais de F8.1 na matriz/baseline), **#6** (árvore final fechada: após os artefatos do #5 em `evidence/v9-baseline/` e as linhas desta triagem/baseline que registram o #5), **#7** (árvore com os registros completos das rodadas #5 e #6: logs/relatórios em `evidence/v9-baseline/` e as linhas da triagem/baseline que os registram), **#8** (árvore final congelada do fechamento: com os registros completos das confirmações 3–5, as linhas #1–#7 desta tabela, as citações das confirmações 2–5 na baseline e a linha F8.2 da matriz atualizada), **#9** (árvore pós-checkpoint `11ca677`/HEAD `85af9ca`: commits PWA-03…07/DOC-02, alterações não commitadas do fechamento SEC-03/TEST-01/02 e respectivas evidências) e **#10** (árvore do fechamento completa — com `v9-fullsuite-final.*`, `v9-meta01-uvlockcheck.log` e `v9-sweep-segredos-docs-pos-sec03.log`): as **#3–#8** com **17 achados e o MESMO conjunto de entradas — nenhum em arquivo novo, editado ou de evidência**; as **#9–#10** com **27 = os 17 canônicos + 10 digests do manifesto F8 (família (e))** — nenhum achado em arquivo criado/editado pelas correções V9 e conjunto de fingerprints idêntico entre a #9 e a #10 (diff vazio em ambos os sentidos). Os reports da #4 e da #6 são byte a byte iguais ao do #2 (`b9520002…`, `cmp` sem diferenças); os das #5, #7 e #8 têm as 17 entradas byte a byte idênticas ao #2 (sha256 canônico das entradas ordenadas por fingerprint `3582b2af…`, igual em todas as execuções) e diferem apenas na ORDEM de serialização das entradas. Cadeia de fechamento: as rodadas de registro foram integralmente reescaneadas pelas varreduras seguintes — os registros do #3 pelo #4; os do #4 (com as rodadas F8.1) pelo #5; os do #5 pelo #6; os do #6 pelo #7; os do #7 pelo #8; os do #8 (log/relatório `confirmacao6`) e as edições documentais F5/F7 pela #9; e tudo o que veio depois da #8 (logs/relatórios da #9, evidências finais do fechamento) pela #10. As únicas mudanças posteriores à #10 são os próprios registros da #10 (log/relatório `confirmacao8`, as linhas #9/#10 desta tabela, a atualização deste item (iv), as novas linhas da baseline e as citações correspondentes) — a MESMA classe de registro que as #9 e #10 acabaram de percorrer inerte (as linhas #1–#8 desta tabela e o item (iv) na forma anterior). O ciclo #2→#10 não introduziu nenhum achado novo de classe ainda não classificada.
- Risco residual: o histórico do repositório (main) contém fixtures falsos que qualquer varredura full-history apontará; estão documentados aqui, comprovadamente falsos (valores mascarados) e nenhum tem valor operacional. Nenhuma credencial real foi encontrada.

## 5. Reprodução

Requisitos: Docker. Caminhos de exemplo iguais aos usados (Windows, Git-Bash; `MSYS_NO_PATHCONV=1` para os bind mounts).

```bash
# 1) Histórico completo — no checkout principal (commitado)
MSYS_NO_PATHCONV=1 docker run --rm -i \
  -v "C:/Users/DZ23/Documents/Qoder/2026-10-12/2f7f19a4/DZ23-Trading-AI:/repo" \
  -v "<dir-evidencias>:/out" \
  ghcr.io/gitleaks/gitleaks:latest detect --source=/repo --log-opts=--all \
  --no-banner --redact --report-format=json --report-path=/out/v9-gitleaks-historico-report.json -v

# 2) Árvore atual — no worktree (inclui alterações não commitadas)
MSYS_NO_PATHCONV=1 docker run --rm -i \
  -v "C:/Users/DZ23/Documents/Qoder/2026-10-12/2f7f19a4/dz23-v9-audit:/repo" \
  -v "<dir-evidencias>:/config" -v "<dir-evidencias>:/out" \
  ghcr.io/gitleaks/gitleaks:latest dir /repo --config=/config/v9-gitleaks-dir.toml \
  --no-banner --redact --report-format=json --report-path=/out/v9-gitleaks-dir-report.json -v

# 3) Reprodução do falso positivo estrutural (.env com valores vazios):
#    criar arquivo sintético em CRLF com as linhas "OPENAI_API_KEY=", "ANTHROPIC_API_KEY=", ...
#    e rodar o modo dir sem --redact: saem os mesmos 2 achados (linhas 1 e 5), com o valor
#    capturado = nome da variável da linha seguinte (mecanismo da expressão regular).
```
