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

## Trocar de servidor sem perder nada

O servidor pode ser descartável; o aprendizado dos agentes não. Duas
proteções, use as duas:

**1. Banco externo (recomendado).** Com `SUPABASE_PROJECT_REF` +
`SUPABASE_DB_PASSWORD` (ou `DATABASE_URL`), o estado da mesa e o histórico
de aprendizado vivem no Postgres, fora da máquina. Ao subir noutro servidor
apontando para o mesmo banco, tudo reaparece. A aba Mesa de Agentes mostra
onde o estado está gravado — se disser "vive no volume deste servidor", você
ainda não tem essa proteção.

**2. Exportar pelo navegador.** Mesa de Agentes → **Exportar tudo (mudar de
servidor)** baixa um arquivo com configuração, aprendizado, estado da mesa e
assinatura.

> **O arquivo não contém segredos.** Chaves de IA e credenciais de corretora
> ficam de fora, de propósito — credencial nunca viaja em texto simples. O
> pacote lista os nomes omitidos em `segredos_omitidos`; guarde-o onde
> guardaria um documento interno (nunca no repositório).

No servidor novo: entre no painel, envie o arquivo em
`POST /v1/mudanca/importar` (ou use o mesmo botão quando estiver disponível
na interface) e **informe as credenciais de novo no painel** (Configuração →
APIs). Importar duas vezes não duplica nada; pacote antigo (formato /1) que
traga chaves tem os segredos descartados e relatados no import.

## Modo da mesa e posse de execução

O painel mostra, na aba Mesa de Agentes, o **Modo da mesa**:

- **Ativa** — executa (paper/testnet; LIVE continua bloqueado).
- **Observador** — analisa e propõe, mas não envia ordem nenhuma: você vê o
  que ela faria sem nada sair.
- **Pausada** — pelo dono, reversível a qualquer momento no painel.
- **Bloqueada** — trava de segurança; sair dela exige o botão de desbloqueio
  com confirmação.

Quem executa é um processo só: a **posse de execução** é tomada pelo processo
que roda os ciclos e renovada a cada ciclo (vence sozinha após 15 min sem
renovação). Com a mesma `DATABASE_URL` nos dois lados, PC e servidor se
enxergam — o segundo processo fica bloqueado com o nome do dono da posse na
mensagem, em vez de operar em dobro.

## PC → Oracle Cloud (ARM64): roteiro de mudança

O PC continua sendo o ambiente principal; o Oracle é a casa de prontidão.
Nada aqui se faz "para produção" sem autorização explícita do dono — sem
deploy em produção e sem mexer em DNS por conta própria.

1. **No PC — pare e leve tudo.** Pare a mesa (botão **Parar**) e baixe o
   pacote no painel (**Exportar tudo**). Ele leva configuração sem segredos,
   aprendizado, estado da mesa e assinatura. O PC fica intacto: nada é
   apagado, e voltar a executar nele é só religar (rollback natural).
2. **Transfira o arquivo** por um canal que você controla. É um JSON sem
   chaves; ainda assim, trate-o como documento interno — nunca no
   repositório. (Se já usa Postgres externo, aponte o Oracle para a MESMA
   `DATABASE_URL`: o estado vive no banco e não há nada a importar.)
3. **No Oracle (aarch64).** Instale Docker e suba o serviço com o
   `docker-compose.yml` (ou só o Dockerfile, em Coolify), definindo
   `ACCESS_PASSWORD` e o volume `/data`. Não exponha a porta direto: TLS/
   reverse proxy na frente. Homologação do Oracle = rodar o ciclo completo
   LÁ com evidência registrada; o Dockerfile existir não homologa nada.
4. **No painel do servidor novo.** Primeiro acesso (o assistente cria a senha
   do dono), importe o pacote em `POST /v1/mudanca/importar` e confira o
   aprendizado restaurado.
5. **Re-informe cada credencial** no painel (Configuração → APIs) — da sua
   gestão de senhas, nunca do pacote: segredo não viaja em texto simples.
6. **Valide antes de deixar rodando.** Confira o cartão **Modo da mesa**
   (deve estar *Ativa* e a posse no servidor novo), rode um ciclo manual e
   leia `/v1/desk/status`: contabilidade balanceada e nada em
   `pendencias_reconciliacao`. Se o PC ainda estiver com a posse, espere o
   vencimento (até 15 min) ou pare o processo dele.
7. **Rollback.** Qualquer dúvida no servidor novo: pare-o e religue a mesa no
   PC. O histórico do PC nunca foi apagado.
