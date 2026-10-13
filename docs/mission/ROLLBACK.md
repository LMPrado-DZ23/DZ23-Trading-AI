# DZ23 Trading AI — ROLLBACK PROCEDURES

## Local Data Rollback
1. Stop any running instance of DZ23 Trading AI (`python -m desktop.launcher` or process).
2. Restore SQLite database backup from `%APPDATA%\DZ23-Trading-AI\jobs.db.bak`.
3. If necessary, delete temporary runtime state files in `%APPDATA%\DZ23-Trading-AI`.

## Git / Branch Rollback
- Last known stable release tag: `v0.4.0-windows-local`
- Integration branch: `feat/integracao-mestre-20261012`
- Reversion command: `git checkout main` or `git reset --hard <commit-sha>`
