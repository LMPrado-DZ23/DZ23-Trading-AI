# FM §9+§10 — Consolidação da auditoria financeira/segurança + revisão READ-ONLY do PR #9

**Task:** #116 (FM-5) · **Branch:** `fix/instalador-atualizacoes-windows`
**Regra da missão (§10):** revisar divergências do PR #9 **somente em leitura**
(`gh pr diff 9` / `gh pr view 9`); **nada** foi mergeado, comentado, votado ou
alterado na branch `audit/claude-financial-security`, no worktree
`dz23-audit-worktree` ou no PR. Cumprida: as únicas operações foram `gh pr
view` / `gh pr diff` (leitura) e os comandos git locais de leitura abaixo.

---

## §1 — Consolidação da auditoria financeira/segurança (tasks #68–#72)

Os 4 achados da auditoria independente do `4b83e16` foram corrigidos e estão
**provados por suíte** (fonte: `TRADING-GATES.md` item 10; evidências por
achado em `docs/mission/evidence/auditoria-4b83e16-*.md`):

| Achado | Fix | Testes | O que prova |
|---|---|---|---|
| SEC-1 segredos em repouso | `a0997df` | 15 | DPAPI (ctypes `crypt32`, sem dependência) no Windows; POSIX 0600 real; verificação de ida-e-volta ANTES de gravar; `SegredoIlegivel` tipado; `status()` honesto no painel |
| FIN-1 LIVE sem selo | `e66f2b7` | 7 | Mesa **recusa** corretora LIVE sem selo de código (falha fechada no código, não em comentário) |
| FIN-2 gates financeiros | `3ee3fe0` | 45 | Matriz adversarial de autorização financeira |
| FIN-3 resposta perdida | `aade521` | 12 | Reconciliação pela identidade DA CORRETORA (resposta perdida não marca ordem viva como ausente) |

**Prova fresca nesta revisão (bateria focada, exit 0):**

```
189 passed in 11,08 s
```

= `test_centro_de_alertas` (42) + `test_alertas_transparencia` (23) +
`test_pwa_push` (36) + `test_pwa_static` (9) +
`test_auditoria_sec1_segredos_em_repouso` (15) +
`test_auditoria_fin1_live_desk_seal` (7) +
`test_auditoria_fin2_gates_financeiros` (45) +
`test_auditoria_fin3_resposta_perdida` (12) = 189.

**Residuais declarados (mantidos, honestos):** inventário rota-a-rota de
autorização não feito integralmente (parcialmente coberto agora pela matriz
FM-2 das 145 rotas + contrato read-only por descoberta da FM-4); registros
legados do OMS pré-`aade521` com reconciliação manual recomendada; DPAPI não
protege do próprio usuário; LIVE segue `BLOCKED_BY_POLICY`.

---

## §2 — Fatos do PR #9 (leitura via `gh`, 2026-10-14)

| Campo | Valor |
|---|---|
| Título | `audit(financial): matriz de achados F3-F6/K1-K6 + prova de recusa do mode=live` |
| Estado | `OPEN`, `MERGEABLE` |
| Head → Base | `audit/claude-financial-security` → `feat/integracao-mestre-20261012` |
| Última atualização | `2026-10-14T10:41:52Z` |
| Tamanho | `+955 / −4`, 8 arquivos |

**Inventário (8 arquivos):** `docs/mission/CLAUDE-01-FINANCIAL-AUDIT.md` (+266),
`service/app.py` (+76/−1), `tests/test_fin_adversarial.py` (+148),
`tests/test_live_execution_blocked.py` (+144),
`tests/test_secret_store_sec1.py` (+125),
`tradingagents/brokers/ccxt_bridge.py` (+11/−3),
`tradingagents/security/__init__.py` (+4),
`tradingagents/security/secret_store.py` (+181).

**Verificação de ancestralidade (fresca, `git merge-base`, nesta revisão):**

- `git merge-base HEAD origin/feat/integracao-mestre-20261012` =
  `30b86838e6cd166af2f5a00becd625caee2127cb` — que **é o próprio tip da base do
  PR #9** → a base do PR está **integralmente contida** na nossa branch.
- `a0997df` (SEC-1): em `HEAD` = **SIM**; em `30b8683` = **NÃO**.
- `e66f2b7` (FIN-1): em `HEAD` = **SIM**; em `30b8683` = **NÃO**.
- `3ee3fe0` (FIN-2): em `HEAD` = **SIM**; em `30b8683` = **NÃO**.
- `aade521` (FIN-3): em `HEAD` = **SIM**; em `30b8683` = **NÃO**.

**Conclusão factual:** o PR #9 propõe correções **paralelas** para achados que
**já estão corrigidos** na branch de entrega (e ausentes na base do próprio
PR). Não há correção nova do trio SEC-1/FIN-1/2/3 que isto justifique merge.

---

## §3 — Divergências por achado (desenho de correção, não diagnóstico)

### SEC-1 — segredos em repouso: dois desenhos concorrentes

| | Nosso (`a0997df`) | PR #9 (`secret_store.py`) |
|---|---|---|
| Cifra | **DPAPI direto via ctypes `crypt32`** (CryptProtectData/CryptUnprotectData) | Fernet (`cryptography`) para os valores + chave-mestra |
| Chave-mestra | Não existe — DPAPI é a proteção | `secret.key` (Fernet) **envelopado por DPAPI só se `win32crypt` disponível**; senão fica em claro 0600 |
| Dependência nova | **Nenhuma** | `cryptography` (já instalada) **+ `pywin32` opcional** (não instalada) |
| Verificação antes de gravar | Ida-e-volta obrigatória; falhou → ABORTA o save | Não equivalente |
| POSIX | 0600 real | 0600 |

**Evidência de ambiente (fresca, no venv onde a suíte roda):**
`.venv/Scripts/python.exe` → **`win32crypt` NÃO instalado**,
`cryptography` instalado, e `segredos_em_repouso.tem_protecao()` = **True**.

**Consequência factual:** na estação Windows do dono, o envelope DPAPI do
PR #9 ficaria **INATIVO** (sem `win32crypt`), caindo no fallback de chave-mestra
em arquivo 0600 — que **no NTFS não protege** (não há semântica POSIX real).
Ou seja: o desenho deles seria **mais fraco** que o nosso justamente na
plataforma-alvo (Windows), além de adicionar dependência. O nosso não depende
de nada e está ativo (`tem_protecao() = True`).

### FIN-1 — LIVE sem selo: comentário × código

- **PR #9:** reescreve docstring de `ccxt_bridge.py` ("Implementação não
  alterada") — a trava é **documental**.
- **Nosso (`e66f2b7`):** a mesa **recusa em código** corretora LIVE sem selo
  (`tradingagents/desk`), com 7 testes que exercitam a recusa.

### FIN-2 / FIN-3 — sobreposição

Os testes deles (`test_fin_adversarial.py`, `test_live_execution_blocked.py`)
atacam a mesma superfície que os nossos (`45` + `12` testes). Diagnóstico
compatível; correção duplicada. Adoção **seletiva de testes** é possível (ver
§6), mas não há correção nova de produto.

### AUTHZ-1 (residual) — parcialmente endereçado fora do PR

O inventário rota-a-rota parcial agora tem: matriz auditável das **145 rotas**
(FM-2, `matriz-rotas-fm2.md`) + contrato read-only **por descoberta** da FM-4
que cobre as 14 rotas manuais. Inventário integral de autorização
(401-rota-a-rota) **segue pendente** — registrado como residual, não ocultado.

### ARCH-1 — `BLOCKED_BY_ARCHITECTURE`

Residual arquitetural declarado (não resolvido por nenhum dos dois lados).
Permanece como está; LIVE `BLOCKED_BY_POLICY`.

---

## §4 — Achados estáticos do PR #9 (LEITURA apenas — não executado)

Marcados como **leitura de código**, não como teste reproduzido (a branch
deles não foi executada; regra §10).

### (a) Esvaziar um segredo pelo painel "ressuscita" o valor antigo

No desenho deles, `save_cfg` monta o cofre com
`segredos = {k: str(v) for k, v in cfg.items() if k in SEGREDOS_DECLARADOS and
isinstance(v, (str, int, float)) and str(v) != ""}` — **string vazia é
excluída do cofre** — e grava o `publico` (sem as chaves de segredo) no
`config.json`. No `load_cfg`, `cfg.update(_secret_store(tenant_id).all())`
reaplica o cofre por cima. Sequência: (1) dono apaga a chave e salva → cofre
não recebe `""` e a chave some do `config.json`; (2) próximo `load_cfg` →
`store.all()` devolve o **valor antigo** e o `update` o **ressuscita**;
(3) o dono não consegue limpar um segredo pelo painel. No nosso desenho,
`""` grava `""` e limpa de verdade (comportamento coberto pelos 15 testes do
`test_auditoria_sec1_segredos_em_repouso`).

### (b) Backup em texto claro persistente

O fluxo deles cria `config.json.bak-pre-secret-store` **em claro** "para
recuperação" e não o remove — segredo antigo permanece no disco indefinidamente
na primeira migração. O nosso não cria backup algum (grava atômico com valores
já cifrados).

### (c) Envelope DPAPI inativo sem `pywin32`

Ver §3/SEC-1 — mesclado lá por ser o mesmo fato.

---

## §5 — Previsão de conflitos de merge (se um dia autorizado)

| Arquivo | Conflito esperado |
|---|---|
| `service/app.py` | `save_cfg`/`load_cfg` (mesmas funções, desenhos diferentes) + imports de segredos |
| `tradingagents/brokers/ccxt_bridge.py` | docstring nas mesmas linhas do nosso selo FIN-1 |
| Módulos de segurança | **Dois** módulos concorrentes: `tradingagents/security/secret_store.py` (deles) × `service/api/segredos_em_repouso.py` (nosso) — escolher UM; manter os dois é convite a divergência de comportamento |

---

## §6 — Recomendação ao dono (decisão é dele; nada foi feito)

1. **NÃO mergear o PR #9 como está** — correções duplicadas e, no Windows,
   proteção **mais fraca** que a já entregue (evidência em §3).
2. **Adoção seletiva possível (opcional):** os arquivos de teste deles
   (`test_fin_adversarial.py`, `test_live_execution_blocked.py`,
   `test_secret_store_sec1.py`) podem ser lidos como material de conferência
   e, se o dono quiser, portados caso a caso sobre a nossa implementação —
   nunca ao contrário.
3. **Fechar/converter o PR** é decisão do dono (não tocamos o PR; §10).
   Recomendação: registrar a decisão em `TRADING-DECISIONS.md` citando este
   documento.
4. **Cortesia/justiça registrada:** o relatório de auditoria deles
   (`CLAUDE-01-FINANCIAL-AUDIT.md`) é minucioso e o **diagnóstico é
   compatível** com o nosso; a divergência é de **desenho de correção**, não
   de leitura dos problemas.

## Limitações declaradas desta revisão

1. **Revisão estática** — o código do PR #9 **não foi executado** nesta
   estação; achados do §4 são leitura de código rotulada como tal.
2. Fatos de ambiente (win32crypt ausente etc.) foram medidos **no venv desta
   estação** — se o dono instalar `pywin32` isso muda para a branch deles,
   mas **não** muda o fato de a nossa entrega estar ativa sem dependência.
3. Nenhuma operação de escrita foi feita no PR #9, na branch deles ou no
   worktree de auditoria.
