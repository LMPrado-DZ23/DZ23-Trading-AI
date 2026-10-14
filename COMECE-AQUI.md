# COMECE AQUI — DZ23 Trading AI

Guia do usuário final. Tudo pelo painel, sem terminal.

## 1. Instalar e entrar (uma vez, ~5 min)

Use o instalador próprio do Windows — artefato `dz23-windows-x64` do
pipeline deste repositório (`DZ23-TradingAI-Setup-<versão>.exe`; confira o
`SHA256SUMS`). A publicação de Release oficial aguarda aprovação do
proprietário. O instalador não exige Python no computador e preserva seus
dados nas atualizações.

Na primeira abertura, o assistente cria a senha do dono e mostra o
**código de recuperação** uma única vez — guarde-o em local seguro.

## 2. Primeiros passos (o painel guia você)

A página **Início** abre no modo **Simples**, com três passos:
**CONECTAR → MODO → PLAY**. Um seletor no topo alterna para **Avançado**,
que revela as demais páginas — nada foi removido; o Avançado só mostra mais.

1. **CONECTAR** — escolha o provedor de IA e cole a chave (Groq é grátis:
   console.groq.com/keys). Opcional: corretora; use sempre chave com
   **saque desabilitado**.
2. **MODO** — escolha a fonte de dados: "Dados de treino (rotulado)" para
   conhecer o sistema, ou "mercado real" (leitura pública, somente leitura).
3. **PLAY** — ligue a mesa em **PAPER** e acompanhe os ciclos.

## 3. O que você tem no painel

- **Mesa de Agentes** — 10 traders (3 homologados + 7 experimentais), o
  Painel Global do Maestro, sala individual de cada trader com gráficos, e
  o motivo por escrito sempre que alguém não opera.
- **Escola** — salas independentes por corretora; a formação de pilotos
  nunca ativa negociação real.
- **Minhas Operações** — copiloto manual: analisa, propõe e acompanha, mas
  **nunca envia ordens**; o registro da sua operação é sempre seu.
- **Alertas** — avisos reais no Windows; o push para celular (PWA) tem o
  mecanismo pronto, com homologação em aparelho físico ainda pendente.
- **Configuração Inteligente (Smart Setup)** — diagnóstico por provedor:
  configurado, conectado, autenticado, operacional.
- **Fechar aplicativo** — encerra o servidor com confirmação (modo Dono
  Único). Use este botão em vez de matar o processo.

## 4. Regras de dinheiro (leia uma vez)

- O padrão é **PAPER**: simulado, com dados de treino rotulados e
  corretora simulada.
- **TESTNET** usa dinheiro fictício da exchange de verdade.
- **LIVE (dinheiro real): bloqueado por construção** (fail-closed) — não é
  ativável pela interface e nenhuma aprovação de teste liga execução real.
- Nada aqui é recomendação de investimento; resultado de teste não é
  promessa de lucro.

## 5. Quando algo dá errado

- O painel diz o motivo (por trader e na decisão do ciclo) — leia antes de
  mexer em qualquer coisa.
- **RUNBOOK.md** — incidentes e restauração (ordem incerta, divergência
  contábil, senha bloqueada, chave vazada).
- Esqueceu a senha? Use o código de recuperação no portão de entrada.
