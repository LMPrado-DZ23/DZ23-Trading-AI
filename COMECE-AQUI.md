# COMECE AQUI — TradingAgents DZ23

Guia do usuário final. Tudo pelo navegador, sem terminal.

## 1. Colocar no ar (uma vez, ~5 min)
No Coolify: **New Resource → Dockerfile** apontando para este repositório,
branch **integracao-v2**.
- Variável obrigatória: `ACCESS_PASSWORD` — uma senha **forte** (20+ caracteres).
- Volume obrigatório: **`/data`** (guarda suas chaves, análises e backups).
- Domínio à sua escolha → **Deploy**.

Nada mais é obrigatório. `DATABASE_URL` é opcional; se você configurar e o
banco não responder, **o serviço não cai**: ele avisa no painel e continua
usando o banco local.

## 2. Conferir se subiu
Abra `dz23trading.com.br/healthz`. Deve responder algo como:
`{"status":"ok","app":"tradingagents-operavel/9","armazenamento":"sqlite"}`
- `status: degraded` → o painel mostra o aviso e o que corrigir.
- Nada responde → o contêiner não subiu: veja os logs no Coolify.

## 3. Usar (o painel guia você)
Abra o domínio, digite a senha e siga o quadro **✅ Seu progresso**:
1. **Escolher a IA** — Groq é grátis e leva 1 minuto (console.groq.com/keys).
2. **Dados de mercado** (opcional) — FRED e Alpha Vantage, ambos gratuitos.
3. **Primeira análise** — aba 📈, digite `BTC/USDT` e a data, ▶ Analisar.
4. **Corretora de treino** — chave grátis em testnet.binance.vision.
5. **Ligar a mesa** — aba 🧑‍💼, botão 🤖. Ela passa a operar sozinha, em
   ciclos, mesmo com o navegador fechado.
6. **Backup** — aba 🧑‍💼, botão 💾. Guarde o arquivo fora do servidor.

## 4. O que o sistema faz sozinho depois disso
- Roda ciclos no intervalo configurado (`DESK_INTERVAL_SECONDS`, padrão 5 min).
- Três traders com estratégias diferentes propõem; um árbitro decide; o risco
  aprova ou veta; a execução acompanha e reconcilia; a contabilidade registra.
- Quando não opera, **diz o motivo** na aba da mesa.
- Reinício do servidor: reconcilia as ordens incertas **antes** de retomar.

## 5. Regras de dinheiro (leia uma vez)
- O padrão é **treino**: dados de fixture e corretora simulada.
- **Testnet** = dinheiro fictício da exchange de verdade.
- **Dinheiro real** existe, mas só liga se **você** digitar a frase de
  autorização no painel — e a mesa autônoma **não** opera em real.
- Nada aqui é recomendação de investimento. Resultado de teste não é promessa.

## 6. Quando algo dá errado
Consulte o `RUNBOOK.md`: tabela de incidentes (ordem incerta, divergência
contábil, senha bloqueada, chave vazada, troca de exchange) e o procedimento
de restauração de backup.
