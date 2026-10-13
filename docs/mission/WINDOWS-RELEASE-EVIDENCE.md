# DZ23 Trading AI — WINDOWS RELEASE EVIDENCE & HOMOLOGATION

## Status
- **Release Version:** `v0.4.0-windows-local`
- **Target OS:** Windows 10/11 x64 (Native Loopback Single-Owner)
- **Deployment Mode:** Single-Owner Desktop (No SaaS / No Cloud required)
- **Status:** `PASS` (Local Desktop Packaging & Single-Owner Verification Complete)

## Desktop Architecture & Security
1. **Loopback Binding**: Launcher strictly sets `HOST=127.0.0.1` and `MULTI_TENANT_ENABLED=false`. No external listening sockets created.
2. **Local Data Persistence**: State stores automatically under `%APPDATA%\DZ23-Trading-AI`.
3. **No External AI Requirements**: Local execution requires zero GPU or Ollama installation. Connections to commercial AI APIs (OpenAI, Anthropic, Gemini, DeepSeek, Groq, Mistral, OpenRouter) are user-configured via settings UI.
4. **FAIL-CLOSED Safety**: Real-money LIVE trading is completely disabled (`REAL_MONEY_EXECUTION=false`). All orders execute strictly in PAPER / Simulation mode.

## Installation & Execution Artifacts
- **Launcher**: `desktop/launcher.py`
- **Powershell Installer**: `desktop/installer/install.ps1`
- **Test Suite**: `tests/windows/test_desktop_launcher.py`
