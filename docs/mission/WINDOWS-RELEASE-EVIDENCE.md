# DZ23 Trading AI — WINDOWS RELEASE EVIDENCE & HOMOLOGATION (V6)

## Status
- **Release Version:** `v0.4.0-windows-local-v6`
- **Target OS:** Windows 10/11 x64 (Native Loopback Single-Owner)
- **Deployment Mode:** Standalone Executable / Desktop App (No SaaS / No Cloud / No PyInstaller dependency on target)
- **Status:** `PASS` (Desktop Packaging, Installer & Shortcuts Verified)

## Desktop Packaging & Shortcuts
1. **Executable Builder**: `desktop/packaging/build_exe.py` packages FastAPI backend, static frontend assets, and multiagent runtime using PyInstaller.
2. **Shortcuts**: `desktop/installer/install.ps1` generates shortcuts in both Desktop (`Desktop\DZ23 Trading AI.lnk`) and Start Menu (`Start Menu\Programs\DZ23 Trading AI`).
3. **No Console Window**: Executable is generated in windowed mode (`--windowed` / `pythonw.exe`), preventing automatic terminal popups on launch.
4. **Data Isolation**: User configurations and SQLite databases persist safely in `%AppData%\DZ23-Trading-AI`, separate from application installation binaries in `%LocalAppData%\DZ23-Trading-AI`.

## Verification Evidence
- **Launcher Unit Tests**: `tests/windows/test_desktop_launcher.py` (2/2 PASS).
- **Financial & Reconciliation Tests**: `tests/test_paper_financial_state.py`, `tests/test_reconciliation_state.py` (4/4 PASS).
- **GitHub Actions CI Run #38326023957**: 8/8 Jobs PASS (Python 3.10-3.13, lint, smoke, audit).
