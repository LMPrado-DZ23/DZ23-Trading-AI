# DZ23 Trading AI — WINDOWS RELEASE EVIDENCE & HOMOLOGATION (V7)

## Status

- **Release Version (alvo):** `v0.4.0-windows-local`
- **Target OS:** Windows 10/11 x64 (nativo, loopback, Dono Único)
- **Modo:** Executável empacotado (PyInstaller) + Instalador Inno Setup; sem SaaS/Cloud; sem Python no PC do usuário
- **Status:** `IN_PROGRESS` — **instalação limpa e mecânica do instalador COMPROVADAS em Windows 11 real** (artefato do run `38439685378`); **dois defeitos de runtime encontrados nessa homologação** (launcher polla `/app/healthz`; trava de força-bruta tranca o dono) — **corrigidos no C008**, com re-homologação pendente no novo artefato.
  - Correção V7: a versão anterior deste documento declarava `PASS` citando testes unitários e CI Linux.
    Isso é **insuficiente** (CI Linux não prova instalador Windows) — status corrigido.

## Comprovado até agora (com evidência)

1. **Scripts e pipeline existem e estão versionados**
   - `desktop/launcher.py`, `desktop/packaging/build_exe.py`, `desktop/installer/setup.iss`, `.github/workflows/windows-release.yml`.
2. **Pipeline de build Windows reescrito (commit `e3e6239`)**
   - Action inexistente (`BoringPlanets/inno-setup-action`, 404) substituída por Chocolatey + `ISCC.exe` absoluto.
   - `uv sync --extra desk --extra dev` (garante `uvicorn/psycopg` no bundle) e PyInstaller via `uv run --with pyinstaller`.
   - Push na branch gera **artefatos de CI** verificáveis (`DZ23-Trading-AI-Setup-v0.4.0.exe`, `.zip`, `SHA256SUMS.txt`); Release só em tag `v*` (aprovação humana).
   - **Build concluído: run `38437252278` success** (3m30s): uv sync ✓ → Inno Setup ✓ → PyInstaller ✓ → ISCC ✓ → zip+hash ✓ → upload ✓; job `publish-release` corretamente pulado.
   - Artefato `dz23-windows-x64` (157.294.923 bytes; digest CI `sha256:f40ab26db60f267e9e2f4d47042e08f1f68c331a5a899d13b4f25bb08c2f659f`); baixado localmente: Setup = 69.752.620 bytes, zip = 88.478.463 bytes.
   - Integridade verificada manualmente: Setup `9ce9c33a…` ✓ e zip `148fee9a…` ✓ (batem com o SHA256SUMS do run). **Defeito encontrado:** o SHA256SUMS saía em tabela PowerShell com caminhos truncados (`D:\a\…`) — inverificável com `sha256sum`; workflow corrigido para formato padrão `hash  filename`.
   - **Artefato do C006 (com as correções) verificado:** run `38439685378` success (somente o `build`; `publish-release` pulado como esperado). Novo artefato baixado (157.296.918 bytes): SHA256SUMS no formato certo, porém com CRLF (`Out-File`) — removendo o `\r`, `sha256sum -c` confirma Setup `3a048c6e…` OK e zip `ca9a7330…` OK. Workflow agora grava com `WriteAllText` + LF (defeito fechado de vez no C007); o artefato baixado serve para a homologação (mudança é só de empacotamento de hash).
3. **Testes automatizados no repositório**
   - `tests/windows/test_desktop_launcher.py`, `tests/windows/test_installer_and_packaging.py` (contratos de empacotamento/launcher/workflow).
   - Contratos do painel: `tests/test_frontend_contract.py` (estrutura do `#bootstrapForm` e marcadores do modo Dono Único).
4. **CI Linux matriz 3.10–3.13 100% verde** nos HEADs recentes (ex.: runs `38422314837`, `38436932376`).
5. **E2E de navegador real (estação Windows 11, servidor local) — painel homologado com o dono**
   - Login do dono na tela "Entrar na sua mesa" → painel "Visão geral" (READY, ledger SQLITE, 22 agentes, papel ADMIN); sessão sobrevive a reload.
   - Trading Floor embutido no painel renderiza (iframe same-origin; `SAMEORIGIN` só em `/floor`, `DENY` no resto) — screenshot capturado.
   - Defeito crítico corrigido no percurso: o formulário de tenant aparecia no modo Dono Único e o botão/token de bootstrap ficavam sempre visíveis (ver C006 em `TRADING-CHECKPOINTS.md`).
6. **Instalador per-usuário sem UAC** (correção C006): `PrivilegesRequired=lowest` + ícone em `{userdesktop}` — o dono leigo instala sem elevação; pendente de prova no próximo artefato.
7. **Senha do dono criada na 1ª execução** (correção C006, achado na inspeção do bundle): o launcher definia o servidor sem `ACCESS_PASSWORD` — o executável empacotado trancaria o dono (todo acesso autenticado respondia 500). Agora `ensure_owner_password()` cria a senha, salva em `%APPDATA%\DZ23-Trading-AI\owner_password.txt` e mostra em diálogo nativo; o painel abre direto em `/app`. Prova: 2 testes novos em `tests/windows/test_desktop_launcher.py`.
8. **Suíte E2E versionada (C007):** `tests/e2e/` (Playwright + servidor uvicorn real, DATA_DIR temporário, Dono Único) — 5 testes verdes em navegador real cobrindo login do dono, menu completo (8 páginas), formulários, validação, floor embutido, governança quant/IA e contratos 4xx/401 com zero 5xx. Sem playwright no ambiente, a suíte pula explicitamente (CI Linux intacta). Screenshots: `docs/mission/evidence/e2e/01-visao-geral-dono.png`, `02-floor-embutido.png`, `03-mesa-quant.png`. (C008: 6 testes — nova regressão do portão de login com 16 s de espera.)
9. **HOMOLOGAÇÃO REAL NA ESTAÇÃO WINDOWS 11 — artefato do run `38439685378`** (Setup `DZ23-Trading-AI-Setup-v0.4.0.exe`, 69.755.620 bytes; zip 88.477.184 bytes; `sha256sum -c` OK após remover CRLF — defeito do SHA256SUMS já corrigido no C007). Todos executados nesta máquina, com o binário baixado do CI:

   | # | Passo | Resultado | Evidência |
   |---|-------|-----------|-----------|
   | 1 | Instalação silenciosa `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART` | **PASS** (exit 0) | `{localappdata}\DZ23-Trading-AI\DZ23-Trading-AI.exe` + `unins000.exe` criados |
   | 2 | Atalhos criados | **PASS** | Desktop `DZ23 Trading AI.lnk` + Menu Iniciar `Programs\DZ23 Trading AI\DZ23 Trading AI.lnk`, ambos apontando para o exe |
   | 3 | Runtime do bundle instalado | **PASS** (com ressalvas do C008) | `healthz` 200, `/app` 200, `/readyz` READY/PAPER, signup 404; janela sem console (build `--windowed`) |
   | 4 | Instalação sem UAC | **PASS** | `PrivilegesRequired=lowest`; nenhuma elevação pedida |
   | 5 | Reinstalação sobre instalação existente | **PASS** (exit 0, dados intactos) | mtime idêntico de `agency.db`/`jobs.db`/`subscriptions.db` (13/10 14:47:26) e `owner_password.txt` (14:41:01, 16 B) em `%APPDATA%\DZ23-Trading-AI` |
   | 6 | Desinstalação | **PASS** (exit 0) | pasta do programa removida por completo; os 2 atalhos removidos; **dados do dono preservados** em `%APPDATA%` |
   | 7 | Separar binário (`%LOCALAPPDATA%`) de dados (`%APPDATA%`) | **PASS** | exe em `{localappdata}\DZ23-Trading-AI`; SQLite/senha em `%APPDATA%\DZ23-Trading-AI` |

   **Defeitos encontrados NESTA homologação (não apareceriam só com CI verde), corrigidos no C008 com red-proof:**
   - **Launcher pollava `/app/healthz` (404)** → abria o painel ~15 s atrasado com aviso falso de timeout (log real do build de diagnóstico: `GET /app/healthz 404` ×30). Corrigido: `_healthz_ok` + um único caminho de poll na raiz + fast-path de instância única.
   - **Trava de força-bruta trancava o dono (crítico):** polling anônimo do iframe do floor armava a trava de 15 min por IP; dono que demora >15 s recebia 429 com a senha CERTA. Corrigido: 401 anônimo não conta + floor não polla sem sessão ("AGUARDANDO LOGIN"). Reproduzido e fechado no bundle real.
   - **Nota de contexto de execução (não é defeito):** processos lançados de comando shell de vida curta são mortos ao terminar (job object); lançamento em background persistente mantém o app vivo (pid estável, `healthz` 200).

   **Pendente com o NOVO artefato (C008):** refazer o ciclo instalando, **abrindo como usuário real (duplo clique no atalho)**, login do dono no navegador sem pressa, instância duplicada abre o painel existente, e registrar aqui.

## Ainda NÃO comprovado (pendências de homologação)

- [x] Instalação limpa em Windows 10/11 x64 real com o artefato gerado pelo CI (run `38439685378`, exit 0 — item 9).
- [x] Criação de shortcuts (Desktop/Start) pelo instalador — item 9.
- [x] Início sem janela de console; healthz respondendo após abrir — item 9 (com re-prova no C008: abertura do painel sem os 15 s falsos de timeout).
- [x] Migração de versão (reinstalação sobre a anterior) e uninstaller que preserva dados em `%APPDATA%` — item 9.
- [x] Separar binários (`%LOCALAPPDATA%`) de dados (`%APPDATA%`) verificado em runtime — item 9.
- [x] Hash SHA256 dos artefatos verificados (via `sha256sum -c` no artefato do C006 após normalizar CRLF; formato do workflow corrigido no C007).
- [ ] Bloqueio de instância duplicada; comportamento com porta ocupada — **corrigido no C008** (fast-path abre o painel existente; teste unitário verde); re-prova no bundle novo pendente.
- [ ] Abertura como usuário real (duplo clique no atalho) + login do dono no navegador no bundle CORRIGIDO (C008) — pendente com o novo artefato.
- [ ] Encerramento limpo, sleep/wake, offline/reconexão, recuperação de crash.
- [ ] Assinatura de código: somente com certificado autorizado (não há certificado disponível).

## Próximo passo (executado nesta estação Windows 11)

1. Push do batch C008 → novo run de build com launcher/floor/trava corrigidos.
2. Baixar o novo artefato `dz23-windows-x64`.
3. Ciclo completo no bundle corrigido: instalar → abrir pelo atalho (usuário real) → login do dono sem pressa no navegador → duplo clique de novo (instância única) → reinstalar → desinstalar preservando dados.
4. Registrar cada item acima como PASS/FAIL com data, run id e caminhos — sem declarar homologação antes disso.

*Última atualização: 2026-10-13 — batch C008 sobre `0e6fdeb`, branch `feat/integracao-mestre-20261012`.*
