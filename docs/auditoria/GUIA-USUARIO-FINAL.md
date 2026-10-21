# DZ23 Trading AI — Guia simples do proprietário

Aplicativo pessoal de análise e trading **PAPER/TESTNET** no Windows. Dono único.
**Dinheiro real está bloqueado** nesta versão — nada opera sua conta de verdade.

## 1. Instalar
- Use o instalador Windows gerado pelo CI (Setup `.exe`). Confira o `sha256` do
  arquivo antes de instalar (está na evidência de release).
- Observação: enquanto não houver **certificado de assinatura de código**, o
  Windows pode mostrar um aviso do SmartScreen ("Mais informações → Executar
  assim mesmo"). É esperado; não significa vírus.
- A instalação não precisa de Python nem de terminal.

## 2. Primeira execução (criar a senha do dono)
- Abra o app pelo atalho. Na primeira vez, o app pede para **criar a senha do
  dono** e mostra **UMA vez** um **código de recuperação** — anote e guarde.
- Depois disso, toda abertura pede essa senha (o painel é só seu, neste PC).
- Esqueceu a senha? Use "Esqueci minha senha" com o código de recuperação.

## 3. Conectar (tela Início → 1. CONECTAR)
- **IA (opcional, recomendado):** escolha um provedor (ex.: **Groq** é grátis em
  console.groq.com/keys), cole a chave e salve. A chave é **cifrada em repouso**
  (DPAPI do Windows) — não fica em texto puro no disco.
- **Corretora (sandbox):** para PAPER/TESTNET, gere uma chave no **Binance Spot
  Testnet** (testnet.binance.vision) e cole em API Key / API Secret. Use
  **somente leitura** sempre que possível.
- Clique **Salvar conexões**. O app testa e mostra o status
  (configurado → conectado → autenticado) — sem nunca exibir o valor da chave.

> ⚠️ Nunca cole a chave da sua conta **real** se quiser só testar — use a do
> **testnet**. A chave real só serve para leitura da conta real, e é sua
> decisão colá-la no painel (nunca em chat/arquivo compartilhado).

## 4. Operar em PAPER
- Vá em **Análises/Operações**, escolha um ativo (ex.: BTC/USDT), rode a primeira
  análise e inicie o **PAPER**. Tudo é simulado: sinal → risco → ordem fictícia →
  fill → taxa → livro-razão → resultado.
- Acompanhe em **Visão geral** (saúde, agentes, execução) e em **Minhas
  Operações**.
- **Pausar/retomar** e **parar** estão sempre disponíveis. O **kill-switch** é
  persistente: se você parar, continua parado mesmo após reiniciar.

## 5. Fechar com segurança
- Use o botão **"Fechar aplicativo"** no topo do painel (encerra o servidor
  local com confirmação) — não feche só a janela do navegador.

## O que está BLOQUEADO de propósito (segurança)
- **Dinheiro real (LIVE):** desligado em várias camadas. Nenhum botão, variável
  de ambiente, configuração salva ou API libera ordem real. Ativar exigiria uma
  decisão humana nova e explícita, fora deste app.
- **Cadastro/cobrança/multiusuário:** não existem (app de dono único).

## Limites honestos (leia antes de confiar dinheiro no futuro)
- **Backtest verde NÃO é lucro.** O sistema inclusive reprova resultados de
  amostra pequena e avisa que "não prevê o futuro". Vantagem financeira só se
  comprova com operação PAPER/TESTNET ao longo do tempo.
- Esta é uma versão para **uso pessoal, PAPER/TESTNET** — não é aconselhamento
  financeiro nem garantia de retorno.

## Problemas comuns
- **"porta ocupada":** outro app já usa a porta; feche-o ou reinicie o PC.
- **IA não conecta:** confira a chave na tela de Configuração e o provedor.
- **Corretora recusa:** verifique se a chave é do **testnet** e tem permissão de
  leitura.
