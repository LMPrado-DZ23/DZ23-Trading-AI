# DZ23 Trading AI — WINDOWS RELEASE EVIDENCE & HOMOLOGATION (V7)

## Status

- **Release Version (alvo):** `v0.4.0-windows-local`
- **Target OS:** Windows 10/11 x64 (nativo, loopback, Dono Único)
- **Modo:** Executável empacotado (PyInstaller) + Instalador Inno Setup; sem SaaS/Cloud; sem Python no PC do usuário
- **Status:** `IN_PROGRESS` — **ciclo completo de homologação executado no artefato CORRIGIDO do CI**: instalar → abrir → login do dono (com espera longa no portão) → instância duplicada → fechar → reinstalar → desinstalar preservando dados — todos **PASS** (item 10). Três defeitos de runtime achados na 1ª homologação (C008) e **re-provados corrigidos no bundle**; saída do usuário (C009) homologada em dev-runner, re-prova no bundle pendente do próximo build.
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

10. **HOMOLOGAÇÃO DO ARTEFATO CORRIGIDO C008 — run `38444558353`** (push de `142e030`). Setup `DZ23-Trading-AI-Setup-v0.4.0.exe` 69.754.365 B `sha256:59403222…`; zip 88.480.081 B `sha256:931f886b…`; **`sha256sum -c SHA256SUMS.txt` → ambos OK direto, sem tocar no arquivo** (formato LF do C007 validado em artefato de produção). Ciclo completo executado na estação Windows 11:

   | # | Passo | Resultado | Evidência |
   |---|-------|-----------|-----------|
   | 1 | Instalação silenciosa sem elevação | **PASS** (exit 0) | exe + `_internal` + `unins000` em `{localappdata}\DZ23-Trading-AI`; atalhos Desktop/Menu Iniciar; registro HKCU `DisplayVersion 0.4.0` |
   | 2 | 1ª abertura como usuário | **PASS** | log do launcher: `GET /healthz 200` na 1ª tentativa → `Local backend is ready. Opening UI...` em ~1,5 s, abrindo `/app` — **sem o timeout falso de 15 s** (defeito C008-1 morto no bundle) |
   | 3 | Headers de runtime | **PASS** | `/app` e `/floor`: `Cache-Control: no-store` ao vivo (defeito C008-3 fechado) + `X-Frame-Options` corretos; `HEAD` não é usado pelo navegador |
   | 4 | Playwright no bundle INSTALADO: floor sem sessão | **PASS** | `AGUARDANDO LOGIN`, **zero** chamadas a `/v1/agency/snapshot` em 8 s (antes pollava a cada 3 s) |
   | 5 | Playwright: portão do dono com 16 s de espera | **PASS** | espera 16 s (>5 ciclos do polling antigo) → login com a senha do dono → painel abre (defeito C008-2, trava 429, morto no bundle) |
   | 6 | Playwright: visão geral + floor embutido | **PASS** | `READY`/`ADMIN`, "Live bloqueado"/`BLOCKED`; iframe do floor `CONNECTED` com a sessão do dono. Capturas: `docs/mission/evidence/windows-release/painel-instalado-c008.png` e `floor-instalado-c008.png` |
   | 7 | Instância duplicada (2ª abertura) | **PASS** | exit 0 em **367 ms**; log `Já existe uma instância servindo http://127.0.0.1:8000 — abrindo o painel.`; pid do listener inalterado (38320) — sem segundo backend |
   | 8 | Reinstalação sobre a existente | **PASS** (exit 0) | mtimes de `agency.db`/`jobs.db`/`subscriptions.db`/`owner_password.txt` byte a byte idênticos; senha reutilizada sem novo diálogo; binário reinstalado sobe (`healthz` 200, `/app` 200, `/floor` 200) |
   | 9 | Desinstalação preservando dados | **PASS** (exit 0) | pasta do programa e atalhos removidos; `%APPDATA%\DZ23-Trading-AI` intacto (mtimes idênticos) |
   | 10 | Sem janela de console | **PASS** | PE Subsystem do exe dentro do zip = **2 (WINDOWS/GUI)**; nenhum console apareceu nos lançamentos |

   **Notas de execução:** o instalador rodado do Git Bash exige `MSYS_NO_PATHCONV=1` (sem isso o MSYS converte `/VERYSILENT` em caminho e abre o wizard interativo — comportamento do shell, não do instalador). Fechamento entre passos via `taskkill` (não havia caminho de saída pelo app — ver C009 abaixo).

11. **C009 — saída do usuário (achado desta homologação):** executável windowed sem console e sem bandeja; fechar o navegador não para o servidor — o dono ficaria preso ao Gerenciador de Tarefas. Implementado: botão **"Fechar aplicativo"** no painel (visível só no modo Dono Único) → `POST /v1/app/quit` (auth do dono; 404 em multi-tenant ANTES da autenticação; 409 se o servidor não foi iniciado pelo launcher) → `uvicorn.Server.should_exit` → encerramento gracioso. Prova em dev-runner (mesma topologia): clique no navegador real → `POST /v1/app/quit 200` → `Application shutdown complete` → **launcher exit 0 sozinho**, porta 8000 livre; captura `docs/mission/evidence/windows-release/encerrado-c009.png`. 4 testes unitários + 1 de contrato + 2 de launcher (red→green). **Re-prova no bundle pendente do próximo build (C009).**

## Ainda NÃO comprovado (pendências de homologação)

- [x] Instalação limpa em Windows 10/11 x64 real com o artefato gerado pelo CI — ciclo completo no artefato C008 (item 10).
- [x] Criação de shortcuts (Desktop/Start) pelo instalador — item 9 (C006) e item 10 (C008, recriados).
- [x] Início sem janela de console (PE Subsystem=GUI), healthz respondendo e painel abrindo sem timeout falso — item 10.
- [x] Migração de versão (reinstalação sobre a anterior) e uninstaller que preserva dados em `%APPDATA%` — item 10.
- [x] Separar binários (`%LOCALAPPDATA%`) de dados (`%APPDATA%`) verificado em runtime — item 9.
- [x] Hash SHA256 dos artefatos verificados — `sha256sum -c` OK direto no artefato C008 (formato LF validado em produção).
- [x] Bloqueio de instância duplicada — item 10, passo 7 (exit 0 em 367 ms, listener único).
- [x] Abertura como usuário + login do dono no navegador sem pressa (16 s no portão) no bundle CORRIGIDO — item 10, passos 2–6.
- [x] Encerramento limpo com **saída pelo próprio app** — C009 (item 11): botão "Fechar aplicativo" + shutdown gracioso; re-prova no bundle C009 pendente do próximo build.
- [ ] Porta ocupada por OUTRO processo (não-DZ23) — parcialmente coberto pelo fast-path (se healthz responde, abre; senão tenta subir). Cenário hostil fica registrado como risco aceito no log de erro do uvicorn.
- [ ] Sleep/wake, offline/reconexão e recuperação de crash — não exercitados (fora do alcance desta estação sem suspender a máquina do dono).
- [ ] Assinatura de código: somente com certificado autorizado (não há certificado disponível).

## Próximo passo (executado nesta estação Windows 11)

1. Push do batch C009 → build de CI → baixar artefato novo.
2. Re-provar no bundle C009: instalar → abrir → **encerrar pelo botão "Fechar aplicativo"** → confirmar processo encerrado e porta livre → desinstalar preservando dados.
3. Se o ciclo fechar verde, restam apenas sleep/wake/offline/crash e assinatura de código — declarar o status honesto de cada um.

*Última atualização: 2026-10-13 — batch C009 sobre `142e030`, branch `feat/integracao-mestre-20261012`.*
