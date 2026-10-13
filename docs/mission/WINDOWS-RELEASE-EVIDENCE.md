# DZ23 Trading AI — WINDOWS RELEASE EVIDENCE & HOMOLOGATION (V7)

## Status

- **Release Version (alvo):** `v0.4.0-windows-local`
- **Target OS:** Windows 10/11 x64 (nativo, loopback, Dono Único)
- **Modo:** Executável empacotado (PyInstaller) + Instalador Inno Setup; sem SaaS/Cloud; sem Python no PC do usuário
- **Status:** `PASS` — **ciclo final de homologação executado no ARTEFATO FINAL** (run `38450504893`, bundle do merge `0c3fc26` com C008+C009+C010): instalar → abrir sem timeout falso → login do dono após 16 s no portão → **encerrar pelo botão "Fechar aplicativo"** (shutdown gracioso, launcher exit 0 sozinho, porta livre) → **intruso real na 8000 → diálogo nativo de porta ocupada → WM_CLOSE → exit limpo** → reinstalar (dados byte a byte idênticos) → desinstalar (dados preservados). **Re-provado no BUNDLE DA ENTREGA M01** (run `38453302293`, push de `d6491ab`): mesmo ciclo verde + o painel **"Mesa de Agentes" com os 10 traders por tier de evidência** renderizado ao dono no bundle instalado (3 homologados → "aprovado (paper)"; 7 experimentais → "experimental (paper)") — item 13. Ressalvas honestas: sleep/wake, offline/reconexão e crash não exercitados nesta estação; assinatura de código `BLOCKED_BY_EXTERNAL_DEPENDENCY` (sem certificado).
  - Correção V7: a versão anterior deste documento declarava `PASS` citando testes unitários e CI Linux.
    Isso é **insuficiente** (CI Linux não prova instalador Windows) — status corrigido; o PASS atual só cita o ciclo real no laptop do dono.

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

11. **C009 — saída do usuário (achado desta homologação):** executável windowed sem console e sem bandeja; fechar o navegador não para o servidor — o dono ficaria preso ao Gerenciador de Tarefas. Implementado: botão **"Fechar aplicativo"** no painel (visível só no modo Dono Único) → `POST /v1/app/quit` (auth do dono; 404 em multi-tenant ANTES da autenticação; 409 se o servidor não foi iniciado pelo launcher) → `uvicorn.Server.should_exit` → encerramento gracioso. Prova em dev-runner (mesma topologia): clique no navegador real → `POST /v1/app/quit 200` → `Application shutdown complete` → **launcher exit 0 sozinho**, porta 8000 livre; captura `docs/mission/evidence/windows-release/encerrado-c009.png`. 4 testes unitários + 1 de contrato + 2 de launcher (red→green). **Re-provado no BUNDLE FINAL — item 12, passos 4–5.**

12. **HOMOLOGAÇÃO FINAL DO BUNDLE — run `38450504893` (artefato do merge `0c3fc26`, com C008+C009+C010).** Setup `DZ23-Trading-AI-Setup-v0.4.0.exe` **69.759.848 B** `sha256:bd160830a51a46222270317e932772bb82b9313054504410071d4e4848ae74de`; zip 88.482.294 B `sha256:2cd7ebc2f7d244707b8f51adc54a8d80f0e4fb8b331188f7777780ef3ab822a5`; **`sha256sum -c SHA256SUMS.txt` → ambos OK direto, sem tocar no arquivo.** Ciclo único executado na estação Windows 11:

   | # | Passo | Resultado | Evidência |
   |---|-------|-----------|-----------|
   | 1 | Verificação de integridade do artefato | **PASS** | `sha256sum -c` OK nos 2 arquivos (formato LF do C007 validado de novo em produção) |
   | 2 | Instalação silenciosa sem elevação | **PASS** (exit 0) | exe + `_internal` + `unins000` em `{localappdata}\DZ23-Trading-AI`; atalhos Desktop/Menu Iniciar |
   | 3 | 1ª abertura como usuário | **PASS** | log do launcher: `GET /healthz 200` na 1ª tentativa → `Local backend is ready. Opening UI...` em ~1,5 s; `/app` 200; **sem timeout falso** |
   | 4 | Playwright (Chrome real) no bundle: portão do dono + login | **PASS** | espera de **16 s** no portão (anti-trava 429) → login com a senha do dono → painel `ADMIN`, "Live bloqueado", botão "Fechar aplicativo" visível. Captura: `docs/mission/evidence/windows-release/painel-final-item12.png` |
   | 5 | Encerramento PELO BOTÃO "Fechar aplicativo" (C009 no bundle) | **PASS** | diálogo nativo de confirmação aceito → `POST /v1/app/quit 200` → `Application shutdown complete` → `Finished server process [65312]` → **launcher encerrou sozinho, exit 0** → porta 8000 livre, processo morto. Captura: `encerrado-item12.png` |
   | 6 | Porta 8000 ocupada por OUTRO processo + duplo clique (C010 no bundle) | **PASS** | intruso real (socket Python, pid 57876) escutando 127.0.0.1:8000 → launcher detecta, espera a 1ª instância (15 s), **não abre navegador** e mostra diálogo nativo **"DZ23 Trading AI — porta ocupada"** (título encontrado por EnumWindows, classe `#32770`); captura `porta-ocupada-item12.png`; descarte por **WM_CLOSE** → **exit 0 limpo** |
   | 7 | Reinstalação sobre a existente | **PASS** (exit 0) | `agency.db`/`jobs.db`/`subscriptions.db`/`owner_password.txt` **byte a byte idênticos** (`sha256sum -c` OK pós-reinstalação) |
   | 8 | Binário reinstalado sobe e desliga graciosamente | **PASS** | `healthz` 200 em 1 s → `POST /v1/app/quit` com Bearer do dono → 200 → processo encerra (exit 0) |
   | 9 | Desinstalação preservando dados | **PASS** (exit 0) | pasta do programa e atalhos Desktop/Menu removidos; `%APPDATA%\DZ23-Trading-AI` **byte a byte idêntico** (`sha256sum -c` OK pós-uninstall) |
   | 10 | Sem janela de console | **PASS** | nenhum console apareceu em todos os lançamentos (PE Subsystem=GUI do C008 mantido) |

   **Nota de execução (harness, não defeito do app):** o Playwright por padrão auto-dispensa `confirm()` — o clique no botão só completou após aceitar o diálogo de confirmação do navegador; o app pergunta ao dono antes de encerrar (comportamento desejado).

13. **HOMOLOGAÇÃO DO BUNDLE DA ENTREGA M01 — run `38453302293`** (push de `d6491ab`: M01 mergeado por fast-forward na branch de integração). Artefato `dz23-windows-x64` (157.319.893 B); Setup 69.765.446 B `sha256:bed915cf…`; zip 88.490.105 B `sha256:fdb8d10a…`; **`sha256sum -c SHA256SUMS.txt` → ambos OK direto** (formato LF mantido). Ciclo executado na estação Windows 11 com o binário baixado do CI:

   | # | Passo | Resultado | Evidência |
   |---|-------|-----------|-----------|
   | 1 | Integridade dos artefatos | **PASS** | `sha256sum -c` direto no diretório baixado: Setup `bed915cf…` OK, zip `fdb8d10a…` OK |
   | 2 | Instalação silenciosa sem elevação | **PASS** (exit 0) | instalação limpa (pasta do programa não existia); exe + `_internal` + `unins000` em `{localappdata}\DZ23-Trading-AI` |
   | 3 | 1ª abertura | **PASS** | `healthz` 200 na 1ª tentativa; `/readyz` `READY`/`PAPER`, `kill_switch=false` |
   | 4 | Playwright no bundle: portão do dono com **16 s** de espera | **PASS** | espera 16 s → senha do dono → badge `ADMIN` (trava 429 morta também no bundle M01) |
   | 5 | Playwright: painel **"Mesa de Agentes"** com os 10 traders por evidência | **PASS** | "Traders (10 perfis)" com **exatamente 10 linhas**: 3 homologados → **"aprovado (paper)"** (trend/reversion/breakout) e 7 experimentais → **"experimental (paper)"** (scalping/swing/momentum/volatility/arbitrage/statistical/adaptive) — o tier vem do mandato (habilitação só por evidência). Captura: `docs/mission/evidence/windows-release/mesa-m01-item13.png` |
   | 6 | Encerramento pelo botão "Fechar aplicativo" | **PASS** | diálogo nativo "Encerrar o aplicativo DZ23?…" aceito → `POST /v1/app/quit 200` → **processo do launcher exit 0 sozinho** → porta 8000 livre (`healthz` 000). Captura: `encerrado-m01-item13.png` |
   | 7 | Reinstalação sobre a existente | **PASS** (exit 0) | mtimes de `agency.db`/`jobs.db`/`subscriptions.db`/`owner_password.txt` byte a byte idênticos; binário reinstalado presente |
   | 8 | Desinstalação preservando dados | **PASS** (exit 0) | pasta do programa removida; `%APPDATA%\DZ23-Trading-AI` intacto (mtimes idênticos) |

   **Nota de execução:** o mesmo harness Playwright do item 12 (aceita o `confirm()` do botão antes de seguir) — comportamento desejado do app, não defeito.

## Ainda NÃO comprovado (pendências de homologação)

- [x] Instalação limpa em Windows 10/11 x64 real com o artefato gerado pelo CI — ciclo completo no artefato C008 (item 10).
- [x] Criação de shortcuts (Desktop/Start) pelo instalador — item 9 (C006) e item 10 (C008, recriados).
- [x] Início sem janela de console (PE Subsystem=GUI), healthz respondendo e painel abrindo sem timeout falso — item 10.
- [x] Migração de versão (reinstalação sobre a anterior) e uninstaller que preserva dados em `%APPDATA%` — item 10.
- [x] Separar binários (`%LOCALAPPDATA%`) de dados (`%APPDATA%`) verificado em runtime — item 9.
- [x] Hash SHA256 dos artefatos verificados — `sha256sum -c` OK direto no artefato C008 (formato LF validado em produção).
- [x] Bloqueio de instância duplicada — item 10, passo 7 (exit 0 em 367 ms, listener único).
- [x] Abertura como usuário + login do dono no navegador sem pressa (16 s no portão) no bundle CORRIGIDO — item 10, passos 2–6.
- [x] Encerramento limpo com **saída pelo próprio app** — C009 (item 11): botão "Fechar aplicativo" + shutdown gracioso; **re-provado no BUNDLE FINAL — item 12, passo 5** (`/v1/app/quit 200`, `Application shutdown complete`, launcher exit 0 sozinho, porta livre).
- [x] Porta ocupada por OUTRO processo (não-DZ23) — **C010**: sonda TCP real + espera da 1ª instância em boot + diálogo nativo de "porta ocupada"/"falha ao iniciar" (antes: falha silenciosa com navegador aberto numa página inexistente). Provado em dev com listener real (`PORTBUSY_LIVE_C010=PASS`) e **re-provado no BUNDLE FINAL — item 12, passo 6** (diálogo nativo encontrado, WM_CLOSE, exit 0 limpo, navegador não abriu).
- [ ] Sleep/wake, offline/reconexão e recuperação de crash — não exercitados (fora do alcance desta estação sem suspender a máquina do dono).
- [ ] Assinatura de código: somente com certificado autorizado (não há certificado disponível) — `BLOCKED_BY_EXTERNAL_DEPENDENCY`.

## Ciclo final — ENCERRADO (esta estação Windows 11)

1. Push dos batches C009+C010 → builds de CI → merge do PR #8 (`0c3fc26`) → build do merge (`38450504893`) → artefato final baixado. **Feito.**
2. Ciclo final no bundle: instalar → abrir → login → **encerrar pelo botão** → processo morto/porta livre → **intruso real na 8000 → diálogo "porta ocupada"** → reinstalar → desinstalar preservando dados. **Todos PASS — item 12.**
3. Ciclo da entrega M01 no bundle (`38453302293`, `d6491ab`): mesmo roteiro + painel "Mesa de Agentes" com os 10 traders por tier de evidência. **Todos PASS — item 13.**
4. Restam apenas sleep/wake/offline/crash (não exercitados nesta estação) e assinatura de código (`BLOCKED_BY_EXTERNAL_DEPENDENCY`) — declarados honestamente acima. G3 fechado com o artefato do item 12 e re-provado com o artefato da entrega M01 (item 13) em `TRADING-GATES.md`. Publicação (tag/Release) segue aguardando autorização explícita do dono.

*Última atualização: 2026-10-13 — homologação do bundle da entrega M01 `38453302293` (artefato de `d6491ab`), item 13; branches `feat/integracao-mestre-20261012` (merge no PR #7) e `feat/qoder-maestro-traders`.*
