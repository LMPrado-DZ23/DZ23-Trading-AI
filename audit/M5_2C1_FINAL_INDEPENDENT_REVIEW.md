# M5.2c1 — revisão independente final

- Commit auditado: `f93eb6214f2c002ea53cb2ae26af643c6de2b1e3`
- Marco: autoridade PIT de corporate actions e FX
- Escopo excluído: aplicação contábil no ledger, reservada ao M5.2c2
- Fronteira: o processo financeiro não executa plugins, imports dinâmicos ou
  Python fornecido por usuário; objetos imutáveis são defesa de aplicação, não
  um sandbox contra código arbitrário no mesmo processo.

## Evidência do implementador

- Ruff integral: verde.
- Suíte integral Windows com captura Python: **1556 passed, 24 skipped, 71
  subtests**.
- Os skips correspondem a integrações externas não configuradas, ao teste live
  do DeepSeek e aos caminhos POSIX de `dirfd`/`renameat2`.

## Revisões independentes

### Arquitetura e compatibilidade

**APROVADO.** Detectou a regressão do default do manifesto após o primeiro
commit. O follow-up `f93eb62` restaurou a omissão de `schema_version` como v1,
mantendo a criação atual explicitamente em v2. A reprodução independente passou
**139 testes**, com 5 skips POSIX.

### Segurança

**APROVADO.** Revalidou custódia de tenant, spec, policies, ledger, dataset,
manifesto, instrumento, moeda e livro FX; forjas de self-digest, swaps de
handles, mutação dos índices, evidência futura/stale/ambígua e exposição de
helpers inferiores foram recusadas. A reprodução relacionada passou **266
testes**, com 5 skips POSIX. A varredura do diff não encontrou credenciais.

### QA e release

**APROVADO.** Confirmou HEAD, árvore rastreada limpa, compatibilidade v1/v2,
ingestão v2 explícita, 10/10 forjas de expectativas recusadas e ausência de
integração antecipada no ledger. A reprodução relacionada passou **266 testes**,
com 5 skips POSIX.

## Limitações aceitas para o próximo sub-marco

- resultados continuam `ineligible_unverified_coverage` e não autorizam
  promoção quantitativa;
- a busca FX possui fallback temporal de pior caso linear sob disponibilidade
  muito atrasada e deve ser medida antes de cargas grandes;
- o fixture v1 ainda é sintetizado; um artefato histórico externo fortalecerá
  a evidência de release;
- splits, dividendos, delisting e caixa corporativo serão aplicados apenas no
  M5.2c2, com transição de sessão atômica e checkpoint ancorado externamente.

## Veredito

**M5.2c1 APROVADO.** Não habilita LIVE, não promove release e não substitui a
auditoria futura do M5.2c2.
