# SEC-1 — Segredos em repouso protegidos de verdade (auditoria ChatGPT, commit 4b83e16)

**Achado (auditoria independente):** `service/app.py` salvava a configuração
inteira em texto claro via `escrever_json_atomico()`; o `chmod(0o600)` não
protege no Windows (altera apenas o atributo SOMENTE-LEITURA, não a ACL) —
qualquer usuário local lia chaves de IA, chaves de corretora e tokens de
alerta direto do disco.

**Correção (commit `a0997df`):** `service/api/segredos_em_repouso.py` —
valores com cara de credencial passam a ser gravados cifrados com a DPAPI do
próprio usuário do Windows (`CryptProtectData` via ctypes, sem dependência
nova), formato `enc:dpapi:v1:<base64>`; no POSIX o arquivo segue 0600 real
(ali o chmod é ACL de verdade). Falha de cifragem ABORTA o save (nunca grava
em claro por fallback); a migração de config legado acontece no primeiro
load com ida-e-volta VERIFICADA antes de gravar; segredo ilegível (outro
usuário/máquina/blob corrompido) permanece no arquivo e é reportado — nunca
apagado.

## Provas

| Evidência | Comando / resultado |
|---|---|
| RED (pré-fix) | 2/2 falham no app pré-fix: `'gsk-...'` legível em claro no disco |
| GREEN (cifra+roundtrip) | `tests/test_auditoria_sec1_segredos_em_repouso.py` — **15 passed** (fresco, 2026-10-14) |
| Regressão da época | 183 passed em config/secrets/desk |
| Ruff | `ruff check` — All checks passed |

```
.venv/Scripts/python.exe -m pytest tests/test_auditoria_sec1_segredos_em_repouso.py -q
# 15 passed
```

## Limitações declaradas (honestidade)

1. **DPAPI é por usuário e por máquina.** Protege contra OUTROS usuários da
   máquina e contra leitura casual do arquivo; o próprio usuário (e qualquer
   processo rodando como ele) decifra. Migrar o config para outro PC exige
   re-colar os segredos no painel — o blob continua no arquivo, ilegível.
   (Documentada no próprio módulo.)
2. **`chmod` no Windows não vira ACL** — por isso a correção não apostou nele;
   a proteção é a cifragem. O status para o painel declara qual proteção está
   ativa (DPAPI no Windows, 0600 no POSIX).
3. Proteção contra o próprio usuário exigiria hardware/HSM — fora de escopo.

## Arquivos

- `service/api/segredos_em_repouso.py` (novo)
- `service/app.py` (`save_cfg`/`load_cfg`)
- `tests/test_auditoria_sec1_segredos_em_repouso.py` (novo)
