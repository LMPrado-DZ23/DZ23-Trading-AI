# RUNBOOK — operar o TradingAgents DZ23

Procedimentos curtos para quem opera o sistema no dia a dia. Tudo pelo
navegador, exceto onde estiver escrito "no servidor".

## Subir do zero
1. Coolify → aplicação apontando para este repositório (build: `Dockerfile`).
2. Variável `ACCESS_PASSWORD` (senha FORTE, 20+ caracteres) e volume `/data`.
3. Opcional: `DATABASE_URL` (PostgreSQL) e `DESK_INTERVAL_SECONDS`
   (padrão 300 = um ciclo da mesa a cada 5 minutos).
4. Abrir o domínio → senha → aba ⚙️ e seguir "🚀 Comece aqui".

## Começar a analisar (leigo, 5 minutos)
1. Provedor de IA **grátis**: Groq (chave em console.groq.com/keys).
2. 💾 Salvar configurações → aba 📈 Operação → ticker + data → ▶ Analisar.
3. Ações usam o pipeline de ações; `BTC/USDT` e similares vão para o de cripto.

## Ligar a mesa de agentes
1. Aba 🧑‍💼 → confira **Fonte de dados** e **Corretora da mesa**:
   - `Fixture` = treino (prova a coordenação, não o mercado).
   - `Mercado real` = velas públicas; teste com **📡 Testar dados de mercado**.
   - Corretora `paper` = simulada; `ccxt` usa o modo configurado na seção de
     corretora (TESTNET por padrão).
2. **🤖 Ligar mesa autônoma** — a partir daí ela roda sozinha; pode fechar o
   navegador. O estado fica em `/data/desk-state.json`.
3. **🛑 Parar mesa** = parada controlada: o laço encerra, as posições
   continuam sob o guardião.

## Quando a mesa não opera
É comportamento esperado quando os critérios não são atendidos. A aba mostra
o motivo por trader ("sem operar: ..."). Causas comuns:
- `dados insuficientes` → feed sem velas suficientes; use 📡 Testar dados.
- `degraded: NetworkError` → o **servidor** não alcança a exchange (firewall
  de saída) ou a exchange está fora; tente outra exchange em ⚙️.
- `já existe posição aberta` → a mesa evita dobrar exposição.

## Backup e restauração
- **Gerar**: aba 🧑‍💼 → 💾 Gerar backup. O pacote vai para
  `/data/backups/dz23-backup-AAAAMMDD-HHMMSS.tar.gz` (config com segredos,
  fila de jobs, estado da mesa, journal). Baixe pelo volume, não por HTTP.
- **Restaurar** (no servidor, máquina nova):
  ```
  python -c "from tradingagents.desk.backup import restore_backup; \
             print(restore_backup('CAMINHO/backup.tar.gz','/data'))"
  ```
  Cada arquivo é conferido por SHA-256; pacote adulterado é recusado e
  **nada** é gravado. Destino já ocupado exige `overwrite=True`.

## Incidentes
| Sintoma | O que fazer |
|---|---|
| Ordem "unknown_reconciliation_required" | Não reenvie. A reserva fica travada de propósito. Ao religar a mesa, ela reconcilia contra a corretora antes de retomar. |
| Divergência contábil | A aba mostra "DESBALANCEADA". Correções são por compensação; nunca edite lançamento. |
| Senha bloqueada (429) | 5 erros por IP ⇒ 15 minutos. Espere ou reinicie o serviço. |
| Suspeita de chave vazada | Revogue na exchange/provedor, gere outra, cole no painel e 💾 Salvar (sem redeploy). |
| Trocar de exchange ou capital | Configure em ⚙️ e salve: a mesa é parada de forma controlada e reconstruída. |

## Limites honestos
- LIVE (dinheiro real) existe, mas **não** é autorizado pelo fluxo da mesa:
  ordens da mesa só saem em paper/testnet.
- Backtest e avaliação prospectiva são separados; resultado de fixture nunca
  é apresentado como desempenho de mercado.
