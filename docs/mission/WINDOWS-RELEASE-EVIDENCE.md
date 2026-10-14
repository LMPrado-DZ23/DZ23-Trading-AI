# DZ23 Trading AI — WINDOWS RELEASE EVIDENCE & HOMOLOGATION (V7)

## Status

- **Release Version (alvo):** `v0.4.0-windows-local`
- **Target OS:** Windows 10/11 x64 (nativo, loopback, Dono Único)
- **Modo:** Executável empacotado (PyInstaller) + Instalador Inno Setup; sem SaaS/Cloud; sem Python no PC do usuário
- **Status:** `PASS` — **ciclo final de homologação executado no ARTEFATO FINAL** (run `38450504893`, bundle do merge `0c3fc26` com C008+C009+C010): instalar → abrir sem timeout falso → login do dono após 16 s no portão → **encerrar pelo botão "Fechar aplicativo"** (shutdown gracioso, launcher exit 0 sozinho, porta livre) → **intruso real na 8000 → diálogo nativo de porta ocupada → WM_CLOSE → exit limpo** → reinstalar (dados byte a byte idênticos) → desinstalar (dados preservados). **Re-provado no BUNDLE DA ENTREGA M01** (run `38453302293`, push de `d6491ab`): mesmo ciclo verde + o painel **"Mesa de Agentes" com os 10 traders por tier de evidência** renderizado ao dono no bundle instalado (3 homologados → "aprovado (paper)"; 7 experimentais → "experimental (paper)") — item 13. **Operação PAPER ponta a ponta NO BUNDLE** (item 14): ciclo real de mercado-fixture pelo painel (20 propostas, 2 entradas, abstenções e veto do Supervisor visíveis), ledger balanceado e sem pendências de reconciliação → fechar pelo botão → relançar → **estado durável restaurado do disco** (2 ordens/posições, contador de ciclos, estágios) — o log dos últimos ciclos é por sessão, por desenho. **Re-provado no artefato pós-C015 (item 15, run `38456790614`):** o painel do dono passou a ter **exatamente 6 páginas** (sem "Plano e pagamento" nem "Administração" — o DOM morto do SaaS saiu de vez, não só escondido), com ciclo PAPER e reinício durável re-provados no mesmo artefato. **C016b + C017 no bundle (itens 16–17, run `38467455263`, artefato de `d8892dc`; Setup `sha256:24b29dc1…` / zip `sha256:865bb4e9…`, `sha256sum -c` OK direto):** seletor **"Fonte de dados"** provado no painel instalado (feed real `PRONTO`, 239 velas BTC/ETH; ciclo real honesto — "snapshot vencido, não vira ordem"); **o defeito do "primeiro Salvar" morreu** (salvar os padrões do painel em instalação nova não reconstrói mais a mesa: ciclos 1→1); e o **portão premium do dono único** homologado ponta a ponta (assistente de 1ª execução, código de recuperação exibido uma única vez, **migração real do `owner_password.txt` → Argon2id sem perder acesso** — a senha antiga continua entrando, marca "DZ23 Trading AI" sem texto morto do SaaS, exatamente 6 páginas). Ressalvas honestas: sleep/wake, offline/reconexão e crash de SO não exercitados nesta estação; assinatura de código `BLOCKED_BY_EXTERNAL_DEPENDENCY` (sem certificado).
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

14. **OPERAÇÃO PAPER PONTA A PONTA NO BUNDLE INSTALADO — item 14** (mesmo artefato do item 13, run `38453302293`). Ciclo real executado no app instalado nesta estação Windows 11, pelo painel e pelas rotas do dono:

   | # | Passo | Resultado | Evidência |
   |---|-------|-----------|-----------|
   | 1 | Cycle no bundle: "Testar dados de mercado" + "Rodar um ciclo" | **PASS** | resumo real `"20 propostas, 2 admitidas, 2 entradas, 0 saídas, 0 vetos"` com abstenções por trader visíveis (sem rompimento; RSI sem sobrevenda; sem arbitragem; sem apoio multi-prazo; **`Veto do Supervisor para evitar exposição duplicada`**) — a cadeia trader → Supervisor → risco → executor trabalhando diante do dono |
   | 2 | Estado financeiro pós-ciclo | **PASS** | ledger **balanceado**; `pendencias_reconciliacao=[]`; disponível `699.70` de `1000.00` (custo das 2 entradas paper debitado) |
   | 3 | Encerramento pelo botão + relançamento | **PASS** | quit 200 → launcher exit 0 sozinho → porta livre → relançar (novo processo) → `healthz` na 1ª tentativa |
   | 4 | **Reinício: estado durável restaurado do disco** | **PASS** | OMS: **2 ordens + 2 posições abertas** (BTC/USDT, ETH/USDT) restauradas; agendador: **ciclos=1**, `ultimo_ciclo` preservado, `pendencias=[]`; ledger balanceado; estágios 3 `aprovado (paper)` + 7 `experimental (paper)` inalterados — nada ficou incerto após o fechamento abrupto |
   | 5 | Encerramento pelo botão (2ª sessão) | **PASS** | quit 200 → launcher exit 0 sozinho; capturas `paper-ciclo-item14.png`, `paper-pos-restart-item14.png` |

   **Contrato durável (precisão honesta):** sobrevivem ao reinício o estado financeiro/operacional (ordens e posições no OMS, ledger, contador/último ciclo do agendador, estágios dos traders, reconciliação). O **log dos últimos ciclos** (`d["cycles"]`, seção "Ciclos" do painel) é **por sessão, por desenho** — re-popula no próximo ciclo. Nota de harness (não defeito): o script de prova inicialmente assumia o log como durável; ajustado ao contrato real.

   **Estado final da estação:** app **instalado e funcionando** (estado de entrega), dados do dono preservados em `%APPDATA%`; **nada foi publicado** (sem tag/Release) e LIVE permanece `BLOCKED_BY_POLICY`.

15. **PAINEL SEM O SaaS MORTO — item 15** (run `38456790614`, artefato de `ba3307d` = C015). Setup 69.765.284 B `sha256:dc5fe05a…`; zip 88.486.429 B `sha256:3c137695…`; **`sha256sum -c SHA256SUMS.txt` → ambos OK direto**. Instalado por cima do existente (dados preservados) nesta estação Windows 11:

   | # | Passo | Resultado | Evidência |
   |---|-------|-----------|-----------|
   | 1 | Integridade + instalação sobre a existente | **PASS** (exit 0) | `sha256sum -c` OK; instalação silenciosa exit 0 sobre o app do item 14 |
   | 2 | 1ª abertura | **PASS** | `healthz` 200 na **1ª tentativa** |
   | 3 | Playwright no bundle: portão do dono com **16 s** de espera | **PASS** | espera 16 s → senha do dono → badge `ADMIN`; portão **sem nenhum campo do SaaS** (`#loginTenant`/`#loginEmail`/`#bootToken`/`#signupForm` = 0 no DOM) |
   | 4 | Menu do dono = **exatamente 6 páginas** | **PASS** | rótulos exatos: Visão geral, Configuração, Análises, Mesa de Agentes, Trading Floor, Risco — **sem** "Plano e pagamento" e **sem** "Administração" |
   | 5 | Navegação completa pelas 6 páginas | **PASS** | cada página ativa com título correto; **zero respostas 5xx** durante todo o uso real |
   | 6 | Nenhum texto morto do SaaS visível | **PASS** | ausentes: "Plano e pagamento", "Administração", "Criar conta", "Entrar com Google", "PIX", "Assinar" |
   | 7 | Mesa de Agentes no artefato C015 | **PASS** | "Traders (10 perfis)": **3 "aprovado (paper)"** + **7 "experimental (paper)"** (tier por evidência intacto). Captura: `c015-painel-6-paginas.png` |
   | 8 | Encerramento pelo botão | **PASS** | diálogo nativo aceito → `POST /v1/app/quit 200` → launcher encerra sozinho |
   | 9 | PAPER ciclo no artefato C015 | **PASS** | ciclo real pelo painel: ledger **balanceado**, `pendencias_reconciliacao=[]`; fonte `fixture` (aviso honesto "prova a mesa, não o mercado"). Captura atualizada: `paper-ciclo-item14.png` |
   | 10 | Reinício: estado durável restaurado | **PASS** | relançar → OMS **2 ordens/posições** (BTC/USDT, ETH/USDT), agendador **ciclos=2**, ledger balanceado, `pendencias=[]`, estágios 3+7 inalterados. Captura atualizada: `paper-pos-restart-item14.png` |
   | 11 | Encerramento (2ª sessão) pelo botão | **PASS** | quit 200 → launcher exit 0; app deixado **instalado** (estado de entrega), sem tag/Release |

   **Notas de execução (harness, não defeito do app):** (a) o print do harness quebrou em cp1252 ao imprimir os ícones do menu — corrigido com `PYTHONUTF8`; (b) a 1ª tentativa do modo "restart" abortou com fechamento inesperado da janela controlada (`TargetClosedError`) enquanto o servidor permanecia saudável (`healthz` 200) — reexecução passou integralmente; registrado por honestidade.

16. **FONTE DE DADOS + A MORTE DO DEFEITO DO "PRIMEIRO SALVAR" — item 16** (run `38467455263`, artefato de `d8892dc` = C017 + kernel fix PR #11 + C016). Setup 69.805.469 B `sha256:24b29dc1…`; zip 88.540.652 B `sha256:865bb4e9…`; **`sha256sum -c SHA256SUMS.txt` → ambos OK direto**. Instalado e exercitado nesta estação Windows 11 — **22/22 PASS** (`HARNESS_EXIT=0`):

   | # | Passo | Resultado | Evidência |
   |---|-------|-----------|-----------|
   | 0 | Sem processo DZ23 residual antes de instalar | **PASS** | `tasklist` conferido |
   | 0b | Estado de "instalação nova" recriado com honestidade | **PASS** | as 3 chaves de mesa (`TRADINGAGENTS_DESK_FEED/EXCHANGE/BROKER_MODE`) saíram do config da estação; backup `config.pre-c016b.json` guardado |
   | 1 | Instalação silenciosa sobre a existente | **PASS** (exit 0 em 7 s) | exe presente |
   | 2 | 1ª abertura + login do dono | **PASS** | `healthz` 200 (2ª sondagem); `/v1/auth/me` 200; badge `ADMIN` |
   | 3 | Seletor **"Fonte de dados"** na página Configuração | **PASS** | 2 opções: `fixture` = "Dados de treino (rotulado)" e `real` = "Mercado real público (somente leitura)" |
   | 4 | Linha de base fixture: ciclo + ledger | **PASS** | ciclos 0 → 1; ledger balanceado (1000) |
   | 5 | **Primeiro Salvar com os padrões do painel NÃO reconstrói a mesa** | **PASS** | **ciclos 1 → 1** — o defeito do "primeiro Salvar" (desk reconstruída só por salvar os defaults do painel) morreu no kernel fix (PR #11) |
   | 6 | Trocar para mercado real → mesa reconstruída | **PASS** | aviso honesto "mercado real (leitura pública, cutoff na vela fechada)"; reconstrução zera o log de ciclos (mesa nova) — esperado |
   | 6a | Diagnóstico do feed real | **PASS** | `estado=PRONTO`; BTC/USDT e ETH/USDT com **239 velas cada**, `qualidade=ok`, até a vela fechada |
   | 6b | Ciclo com dados reais | **PASS** (honesto) | "sem dados elegíveis"; descartados **"snapshot vencido, não vira ordem"** — o app se recusa a operar em dado velho, como deve |
   | 7 | Volta para fixture + ciclo final | **PASS** | aviso "dados de FIXTURE — provam a mesa, não o mercado"; ledger balanceado (disponível 699.70); `pendencias_reconciliacao=[]` |
   | 8 | Encerramento pelo botão | **PASS** | launcher exit 0 sozinho; porta 8000 livre |

   **Notas de execução (harness, não defeito do app):** (a) o passo 0 quebrou com `UnicodeDecodeError` ao ler `tasklist` (cp850) sob `PYTHONUTF8` — corrigido capturando bytes e decodificando `utf-8/replace`; (b) o passo 0b é cirurgia de estação declarada: recria o estado "recém-instalado" sem essas 3 chaves para provar o first-Save de verdade, com backup antes. Capturas: `c016-fonte-dados.png`, `c016-feed-real.png`, `c016-mesa-real.png`, `c016-volta-fixture.png`.

17. **PORTÃO PREMIUM DO DONO ÚNICO — item 17** (mesmo artefato do item 16, `d8892dc`, run `38467455263`) — **24/24 PASS** (`HARNESS_EXIT=0`), com a senha do dono já existente em texto puro (`owner_password.txt`, 16 caracteres) como pré-condição real de migração:

   | # | Passo | Resultado | Evidência |
   |---|-------|-----------|-----------|
   | 0 | Pré-estado | **PASS** | `owner_password.txt` presente (16 caracteres; valor nunca registrado); `owner_credentials.json` ainda não existia |
   | 1 | Reinstalação silenciosa sobre a existente | **PASS** (exit 0) | binário reinstalado presente |
   | 2 | **Instância limpa abre (APPDATA isolado) na 1ª tentativa** | **PASS** | `healthz` 200; `setup_required=true`, `multi_tenant=false`, `live_trading=false` |
   | 2b | Assistente de 1ª execução | **PASS** | formulário de criação visível, login escondido |
   | 2c | **Código de recuperação exibido uma única vez** | **PASS** | formato `XXXXX-…`, 6 grupos × 5 símbolos (mascarado na evidência); captura `c017-wizard-codigo.png` |
   | 2d | Botão "Abrir o painel" bloqueado até marcar "Anotei o código" | **PASS** | libera após confirmar |
   | 2e | Painel abre com a senha recém-criada | **PASS** | badge `ADMIN`; encerra pelo botão (exit 0, porta livre) |
   | 3 | App do dono abre com o APPDATA real | **PASS** | `healthz` 200 |
   | 3b | **Migração real do texto puro** | **PASS** | `owner_password.txt` **removido**; `owner_credentials.json` criado (332 B) |
   | 3c | JSON guarda **Argon2id** (senha e código) | **PASS** | `senha_hash=$argon2id$…`, `codigo_hash=$argon2id$…` — zero texto puro |
   | 3d | **SENHA ANTIGA continua entrando (acesso preservado)** | **PASS** | HTTP 200, `role=admin` |
   | 3e | Status da instância do dono | **PASS** | `setup_required=false`, `multi_tenant=false`, `live_trading=false` |
   | 4 | Portão premium: marca + selos, zero texto morto do SaaS | **PASS** | "DZ23 Trading AI" + selos PAPER MODE / EXECUÇÃO GOVERNADA; nenhum texto proibido visível; captura `c017-portao-premium.png` |
   | 4b | Senha errada → aviso claro em português | **PASS** | "Senha incorreta. Confira e tente de novo — maiúsculas e acentos contam." |
   | 4e/4f | "Esqueci minha senha" abre recuperação; "Voltar ao login" retorna | **PASS** | formulários alternam corretamente no portão |
   | 4c | Sessão abre com `ADMIN` e **exatamente 6 páginas** | **PASS** | Visão geral, Configuração, Análises, Mesa de Agentes, Trading Floor, Risco (ids conferidos); captura `c017-painel-apos-migracao.png` |
   | 4d | Botão "Fechar aplicativo" visível no modo dono | **PASS** | |
   | 5 | Encerramento pelo botão | **PASS** | exit 0, porta livre |

   **Notas de execução (harness, não defeito do app):** (a) a **1ª execução do harness (18:07) falhou no passo 2** — a instância limpa criou os DBs (~2 s) mas nunca respondeu `healthz` em 90 s; **não reproduzível**: reprodução manual com captura de stderr subiu na 1ª tentativa em ~1,5 s (mesmo precedente do C016 run #1; classe "transitório de 1ª execução pós-instalação"); o harness ganhou captura de stdout/stderr por tentativa + **uma** retentativa registrada — na execução final (18:16) os passos 2 e 3 passaram na 1ª tentativa (`tentativas: {step2_fresh: 1, step3_dono: 1}`); (b) o harness tropeçou ao clicar em "Esqueci minha senha" com a sessão já aberta (o link vive no formulário de login, invisível no painel) — reordenado para o portão, antes do login; (c) a estação termina com `owner_credentials.json` (Argon2id) e **sem** `owner_password.txt`; a senha antiga do dono permanece válida.

18. **IDENTIDADE VISUAL E PORTÃO PREMIUM DO DONO ÚNICO — item 18** (artefato de `f26f1ed`, run `38489060888`) — **27/27 PASS** (`HARNESS_EXIT=0`), log `docs/mission/evidence/e2e/c020-5-item18-f26f1ed-r4.log`, capturas `c020-portao-bundle.png` / `c020-portao-1920x1080.png` / `c020-portao-390x844.png` / `c020-portao-360x640.png`:

   | # | Verificação | Resultado | Evidência |
   |---|-----------|-----------|-----------|
   | 1 | App instalado sobe na estação | **PASS** | healthz em 23,4 s, 1ª tentativa (retentativa registrada do item 17 mantida no harness) |
   | 2 | Contrato HTTP da marca | **PASS** | favicon ICO; símbolo e logomarca PNG; manifesto PWA (`DZ23 Trading AI` / `Trading AI` / `/app` / `#061526`); original **404**; dono preservado (`setup_required=false`) |
   | 3 | Estado operacional real | **PASS** | `/v1/status/operacao` com `modos` + `operacao_real` (modo atual `PAPER`) |
   | 4 | Portão premium | **PASS** | símbolo real ≥64 px; wordmark "Trading AI"; subtítulo; zero texto morto do SaaS; selos PAPER MODE / EXECUÇÃO GOVERNADA / DINHEIRO REAL BLOQUEADO vindos do backend (neutro só sem API) |
   | 5 | Senha e acessibilidade | **PASS** | senha mascarada e vazia; toggle alterna `aria-pressed`/tipo; vazio → aviso claro; mensagem de servidor ausente só quando falha |
   | 6 | Responsivo | **PASS** | 1920×1080, 390×844 e 360×640 sem rolagem horizontal nem corte |

   **Notas de execução (harness, não defeito do app):** as 3 primeiras execuções falharam por defeitos do próprio harness — (a) navegação para a raiz `/` (site institucional) em vez de `/app` (o portão vive no painel); (b) `UnicodeEncodeError` do cp1252 estourando no `≥` com stdout canalizado; (c) strict mode do Playwright com 3 `.pwd-eye` no DOM (login/setup/recuperação). Corrigidos em `scripts/homolog_identidade_bundle.py` (UTF-8 forçado; rota `/app`; seletor escopado `#loginForm .pwd-eye`).

19. **MATRIZ DE INICIALIZAÇÃO DA ESTAÇÃO — item 19** (mesmo artefato de `f26f1ed`, run `38489060888`) — **24/24 PASS** (`HARNESS_EXIT=0`), log `docs/mission/evidence/e2e/c021-matriz-f26f1ed-r2.log`:

   | # | Cenário | Resultado | Evidência |
   |---|---------|-----------|-----------|
   | C1 | Estação limpa → inicialização limpa | **PASS** | healthz na 1ª porta da faixa (8000) em 23,1 s; painel `/app` 200; manifesto PWA 200; faixa deixada livre |
   | C2 | Terceiro na 8000 → fallback sem tocar nele | **PASS** | DZ23 sobe na **8001**; o programa estranho **segue vivo** (nada foi encerrado) |
   | C3 | Instância duplicada | **PASS** | a 2ª sai sozinha (exit 0); a 1ª segue saudável |
   | C4 | Faixa inteira ocupada | **PASS** | DZ23 **não finge iniciar**; diálogo nativo "portas ocupadas" visível (3 janelas de topo) com promessa de não encerrar outros programas |
   | C5 | Reinício | **PASS** | reabre normalmente depois de fechado |
   | C6 | Ciclo limpo em APPDATA descartável | **PASS** | assistente (`setup_required=true`) → senha do dono → `POST /v1/app/quit` 200 → **launcher exit 0 sozinho** (nada de taskkill) → porta liberada → `launcher.log` persistido |

   **Notas de execução (harness, não defeito do app):** o cenário C2 falhou na 1ª execução (r1) por defeito do harness — o ouvinte-sonda nunca chamava `accept()`; 4 sondagens enchiam o backlog do `listen()` e as seguintes estouravam o timeout de 0,5 s ("segue vivo" falso-negativo). Causa-raiz provada em isolamento, sem DZ23 (`c021-c2-saturacao-repro.log`: sonda antiga 4 OK → 8 TimeoutError; corrigida 12/12 OK). Correção em `scripts/matriz_inicializacao_win.py` (thread daemon que drena e recusa conexões). O launcher nunca mata terceiros: `desktop/launcher.py` não possui nenhuma syscall de encerramento (grep `taskkill|terminate|\.kill|psutil|os\.kill` → zero ocorrências).

20. **PAINEL AUTENTICADO — ESCOLA CENTRAL E MESA NO BUNDLE — item 21** (mesmo artefato de `f26f1ed`, run `38489060888`) — **27/27 PASS** (`HARNESS_EXIT=0`), log `docs/mission/evidence/e2e/c021-item21-painel-f26f1ed-r3.log`, capturas `c021-item21-mesa-bundle.png` / `c021-item21-escola-bundle.png`:

   | # | Verificação | Resultado | Evidência |
   |---|-----------|-----------|-----------|
   | 1 | Instância descartável sobe | **PASS** | healthz em 23,1 s; assistente (`setup_required=true`) cria a senha do dono — APPDATA temporário, o DATA_DIR real não é tocado |
   | 2 | Login do dono pelo portão do painel | **PASS** | senha do assistente → painel abre com `ADMIN`, estado `READY`, identidade preenchida |
   | 3 | As 6 páginas do painel | **PASS** | Visão geral, Configuração, Análises, Mesa de Agentes, Trading Floor e Risco — ativas, com título correto |
   | 4 | Mesa de Agentes | **PASS** | livro com **10 traders**: **3 homologado** ("aprovado (paper)") / **7 experimental** ("experimental (paper)") |
   | 5 | Ciclo real pelo botão | **PASS** | "Rodar um ciclo": ciclos 0 → 1 · *"20 propostas, 2 admitidas, 2 entradas, 0 saídas, 0 vetos"* · patrimônio 999,80 balanceado |
   | 6 | Escola Central (autenticada) | **PASS** | contextos de certificação; **sala isolada `binance|paper-legacy|spot|paper`** (chave corretora\|conta\|mercado\|modo); pilotos com *"a autorização é ato exclusivo do proprietário"* |
   | 7 | Zero 5xx e encerramento | **PASS** | nenhuma resposta 5xx no fluxo autenticado; `POST /v1/app/quit` 200 → launcher **exit 0 sozinho**, porta liberada |

   **Notas de execução (harness, não defeito do app):** as execuções r1 e r2 falharam por defeitos do próprio harness — (a) comparação case-sensitive contra o `inner_text()` de `h3` com `text-transform: uppercase` (o painel renderiza "TRADERS (10 PERFIS)"/"ESCOLA CENTRAL"; o diagnóstico da camada navegador capturou o DOM real — zero pageerrors, zero requestfailed, zero 5xx — e confirmou mesa/Escola corretas); (b) regex de chave de sala com `\w+`, que não cobria o hífen de `paper-legacy`. Corrigidos em `scripts/homolog_painel_bundle.py` (comparação em caixa baixa; chave validada por partes).

21. **RECUPERAÇÃO DE CRASH (`taskkill /F`) NO BUNDLE — item 22** (artefato de `f07ea47`, run Windows `38494708308`) — **30/30 PASS** (`HARNESS_EXIT=0`), log `docs/mission/evidence/e2e/c021-item22-crash-f07ea47-r3.log`:

   | # | Verificação | Resultado | Evidência |
   |---|-----------|-----------|-----------|
   | 1 | Sessão 1: APPDATA descartável, assistente, senha, ciclo PAPER real | **PASS** | healthz 23,1 s; *"20 propostas, 2 admitidas, 2 entradas, 0 saídas, 0 vetos"*; 2 ordens `filled`; ledger balanceado; patrimônio 999,80 |
   | 2 | Crash à força do processo do harness | **PASS** | `taskkill /F` no PID próprio → exit 1; porta 8000 liberada pelo SO |
   | 3 | Sessão 2: reabertura no MESMO APPDATA | **PASS** | sobe na mesma porta; senha do dono (Argon2id no disco) continua valendo |
   | 4 | Estado durável byte a byte | **PASS** | ordens idênticas (id/estado/qtd/preço); ciclos 1→1 e `ultimo_ciclo` idêntico; posições e instrumentos abertos idênticos (BTC/ETH); **saldos/patrimônio/disponível idênticos (999,80 / 799,80000145)**; ledger balanceado; `pendencias_reconciliacao=[]`; durabilidade íntegra; modo `ativa` preservado |
   | 5 | Encerramento gracioso | **PASS** | `POST /v1/app/quit` 200 → launcher exit 0 sozinho; porta liberada |

   **Defeitos REAIS do app encontrados e corrigidos antes do verde** (o harness r2 no bundle antigo de `f26f1ed` reprovou 2 itens e virou regressão):
   - **(A) risco cego ao livro vivo:** `attach_portfolio_ledger` só existia no restore — no 1º boot o risco lia `mandate.capital`/reservas e, após os fills, `disponível` mostrava 1000 e `instrumentos_abertos` ficava vazio com posições de verdade abertas (capital investido parecia livre).
   - **(B) painel pós-reinício mentindo:** `restaurar_estado` substitui `coord.ledger`, mas `desk["ledger"]`, `positions_provider` e o agendador seguiam presos ao ledger de abertura — após o crash o painel mostrava patrimônio 1000 em vez de 999,80.
   Correção em `service/app.py` `_desk_boot` (commit `f07ea47`): `risk.attach_portfolio_ledger(ledger)` logo após criar o coordinator; `positions_provider=lambda: coord.ledger.open_positions()`; `desk["ledger"] = coord.ledger`. Regressão pinada em `tests/test_livro_vivo_e_reinicio.py` (3 testes; RED→GREEN) + 172 testes das suítes vizinhas verdes. **Nota de execução (operação, não defeito do app):** a 1ª tentativa de r3 rodou contra o bundle ANTIGO porque o `/VERYSILENT` foi mangled pelo Git Bash (MSYS path conversion) e o setup abriu interativo; reinstalação com `//VERYSILENT` + conferência de tamanho/hash do EXE antes de re-rodar — os valores daquela execução eram idênticos aos do r2 (prova de que o binário era o velho).

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
- [x] **Identidade visual e portão premium do dono único no artefato FINAL (item 18)** — re-homologação `27/27 PASS` no bundle de `f26f1ed` (run `38489060888`).
- [x] **Matriz de inicialização completa no bundle instalado (item 19)** — `24/24 PASS`: estação limpa, fallback sem tocar o vizinho, instância única, faixa ocupada com diálogo nativo, reinício e ciclo limpo com assistente+quit — no artefato de `f26f1ed`.
- [x] **Escola Central e mesa autenticadas no painel do bundle final (item 21)** — `27/27 PASS` no mesmo artefato de `f26f1ed`: assistente → login → 6 páginas → 10 traders (3 homologado / 7 experimental) → ciclo real pelo botão (*20 propostas, 2 entradas*) → Escola Central com sala isolada `binance|paper-legacy|spot|paper` e autorização de piloto exclusiva do proprietário → zero 5xx → quit gracioso (launcher exit 0 sozinho).
- [x] **Recuperação de crash (`taskkill /F`) no bundle instalado (item 22)** — `30/30 PASS` no artefato de `f07ea47` (run `38494708308`): estado durável (ordens, ciclos, `ultimo_ciclo`, posições, saldos/patrimônio/disponível, modo) idêntico antes/depois do kill à força; senha do dono persiste; zero pendências de reconciliação. **2 defeitos reais do app encontrados pelo harness e corrigidos** (risco cego ao livro vivo no 1º boot; painel pós-reinício preso ao ledger de abertura — commit `f07ea47`, regressão em `tests/test_livro_vivo_e_reinicio.py`).
- [ ] Sleep/wake e offline/reconexão — não exercitados (fora do alcance desta estação sem suspender a máquina do dono). Recuperação de crash **FECHADA** (item 22).
- [ ] Assinatura de código: somente com certificado autorizado (não há certificado disponível) — `BLOCKED_BY_EXTERNAL_DEPENDENCY`.

## Ciclo final — ENCERRADO (esta estação Windows 11)

1. Push dos batches C009+C010 → builds de CI → merge do PR #8 (`0c3fc26`) → build do merge (`38450504893`) → artefato final baixado. **Feito.**
2. Ciclo final no bundle: instalar → abrir → login → **encerrar pelo botão** → processo morto/porta livre → **intruso real na 8000 → diálogo "porta ocupada"** → reinstalar → desinstalar preservando dados. **Todos PASS — item 12.**
3. Ciclo da entrega M01 no bundle (`38453302293`, `d6491ab`): mesmo roteiro + painel "Mesa de Agentes" com os 10 traders por tier de evidência. **Todos PASS — item 13.**
4. Operação PAPER ponta a ponta no bundle: ciclo real, ledger balanceado, sem pendências de reconciliação, **reinício com estado durável restaurado do disco** (ordens/posições/contador/estágios), encerramento pelo botão nas 2 sessões. **Todos PASS — item 14.** O app ficou **instalado** na estação (estado de entrega).
5. Painel pós-C015 no bundle (`38456790614`, `ba3307d`): menu do dono com **exatamente 6 páginas** (o DOM morto do SaaS — plano/pagamento, administração, cadastro, bootstrap, login social — foi **removido**, não escondido), zero 5xx, PAPER ciclo + reinício durável re-provados. **Todos PASS — item 15.** App deixado **instalado** (estado de entrega).
6. Restam apenas sleep/wake, offline/reconexão e crash de SO (não exercitados nesta estação) e assinatura de código (`BLOCKED_BY_EXTERNAL_DEPENDENCY`) — declarados honestamente acima. G3 fechado com o artefato do item 12 e re-provado com os artefatos da entrega M01 (itens 13–14) e do painel C015 (item 15) em `TRADING-GATES.md`. Publicação (tag/Release) segue aguardando autorização explícita do dono.
7. Artefato pós-C016b+C017 (`38467455263`, `d8892dc`): **item 16 — fonte de dados** (seletor fixture/real no painel instalado, feed real PRONTO com 239 velas, ciclo real honesto com "snapshot vencido, não vira ordem", **kernel fix: primeiro Salvar não reconstrói mais a mesa — ciclos 1 → 1**) e **item 17 — portão premium do dono único** (assistente de 1ª execução, código de recuperação uma única vez, **migração real `owner_password.txt` → Argon2id sem perder acesso**, marca correta sem o SaaS morto, 6 páginas). **22/22 e 24/24 PASS** nos harnesses; app deixado **instalado** (estado de entrega), LIVE `BLOCKED_BY_POLICY`, nada publicado.

8. Artefato pós-C020+C021 (`f26f1ed`, run Windows `38489060888`): **item 18 — identidade/portão premium re-homologado (`27/27 PASS`)** e **item 19 — matriz de inicialização completa (`24/24 PASS`)** no bundle instalado. A matriz provou: estação limpa, fallback sem tocar o vizinho, instância única, faixa ocupada com diálogo nativo, reinício e ciclo limpo com assistente+quit (launcher exit 0 sozinho). App deixado **instalado** (estado de entrega), LIVE `BLOCKED_BY_POLICY`, nada publicado.

9. Mesmo artefato de `f26f1ed` (run `38489060888`): **item 21 — escola/mesa autenticada no painel do bundle** (`27/27 PASS`, `c021-item21-painel-f26f1ed-r3.log`): assistente descartável → login pelo portão → 6 páginas → mesa com 10 traders (3 homologado / 7 experimental) → ciclo real pelo botão (*20 propostas, 2 admitidas, 2 entradas*, ciclos 0→1) → Escola Central com a sala isolada `binance|paper-legacy|spot|paper` e pilotos com autorização exclusiva do proprietário → zero 5xx → `POST /v1/app/quit` → launcher exit 0 sozinho. App deixado **instalado** (estado de entrega), LIVE `BLOCKED_BY_POLICY`, nada publicado.

10. Artefato de `f07ea47` (run Windows `38494708308`): **item 22 — recuperação de crash (`taskkill /F`) no bundle instalado** (`30/30 PASS`, `c021-item22-crash-f07ea47-r3.log`): ciclo real → kill à força → reabertura no mesmo APPDATA com estado durável idêntico (ordens, ciclos, `ultimo_ciclo`, posições BTC/ETH, patrimônio 999,80, disponível 799,80000145), senha persistente, ledger balanceado, `pendencias_reconciliacao=[]` → quit gracioso (launcher exit 0). O harness encontrou **2 defeitos reais do app** (risco cego ao livro vivo; painel pós-reinício no ledger de abertura), corrigidos em `f07ea47` com regressão pinada (`tests/test_livro_vivo_e_reinicio.py`), 172 testes vizinhos verdes e ruff limpo antes da re-homologação. App deixado **instalado** (estado de entrega), LIVE `BLOCKED_BY_POLICY`, nada publicado.

*Última atualização: 2026-10-14 — itens 18, 19, 21 e 22 homologados no bundle instalado: portão premium/identidade 27/27 e matriz de inicialização 24/24 e escola/mesa 27/27 no artefato de `f26f1ed`; recuperação de crash 30/30 no artefato de `f07ea47` (com 2 defeitos reais corrigidos: risco cego ao livro vivo e painel pós-reinício).*
