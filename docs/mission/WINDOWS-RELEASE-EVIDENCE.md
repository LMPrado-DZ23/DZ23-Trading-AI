# DZ23 Trading AI — WINDOWS RELEASE EVIDENCE & HOMOLOGATION (V7)

## Status

- **Release Version (alvo):** `v0.4.0-windows-local`
- **Target OS:** Windows 10/11 x64 (nativo, loopback, Dono Único)
- **Modo:** Executável empacotado (PyInstaller) + Instalador Inno Setup; sem SaaS/Cloud; sem Python no PC do usuário
- **Status:** `IN_PROGRESS` — **instalação limpa em Windows real AINDA NÃO comprovada**
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
   - Integridade verificada manualmente: Setup `9ce9c33a…` ✓ e zip `148fee9a…` ✓ (batem com o SHA256SUMS do run). **Defeito encontrado:** o SHA256SUMS saía em tabela PowerShell com caminhos truncados (`D:\a\…`) — inverificável com `sha256sum`; workflow corrigido para formato padrão `hash  filename` (próximo build já sai correto).
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

## Ainda NÃO comprovado (pendências de homologação)

- [ ] Instalação limpa em Windows 10/11 x64 real com o artefato gerado pelo CI.
- [ ] Criação de shortcuts (Desktop/Start) pelo instalador.
- [ ] Início sem janela de console; healthz respondendo após abrir.
- [ ] Bloqueio de instância duplicada; comportamento com porta ocupada.
- [ ] Encerramento limpo, sleep/wake, offline/reconexão, recuperação de crash.
- [ ] Migração de versão e uninstaller que preserva dados em `%APPDATA%`.
- [ ] Separar binários (`%LOCALAPPDATA%`) de dados (`%APPDATA%`) verificado em runtime.
- [x] Hash SHA256 dos artefatos verificados (em `38437252278`, ainda que o SHA256SUMS precise da correção já aplicada ao workflow).
- [ ] Assinatura de código: somente com certificado autorizado (não há certificado disponível).

## Próximo passo (executado nesta estação Windows 11)

1. Push do batch de correções (C006) → novo run de build com SHA256SUMS em formato padrão e instalador sem UAC.
2. Baixar o novo artefato `dz23-windows-x64`.
3. Instalar silenciosamente, verificar healthz e shortcuts, fechar, reinstalar e desinstalar preservando dados.
4. Registrar cada item acima como PASS/FAIL com data, run id e caminhos — sem declarar homologação antes disso.

*Última atualização: 2026-10-13 — commit C006 sobre `e3e6239`, branch `feat/integracao-mestre-20261012`.*
