# DZ23 TRADING AI — RELATÓRIO PARA O PROPRIETÁRIO (Documento C)

> **Documento C de 3 da entrega final (V8 §19).** Em português simples: o que funciona, o que ainda não
> funciona e como usar o programa. Data: 2026-10-14. Escrito para você, sem termos técnicos desnecessários.

## 1. Resumo em uma página

O DZ23 Trading AI é hoje um aplicativo Windows que você instala, abre, entra com sua senha e usa em modo
**PAPER (simulação)** e **Copiloto Manual**. Ele:

- **Não opera com dinheiro real.** O modo REAL existe na tela, mas fica bloqueado — e continuará bloqueado
  até você autorizar, depois de uma auditoria independente.
- **Cuida do que é dinheiro de verdade:** todo o dinheiro simulado é acompanhado em um "livro-caixa"
  (ledger) conferido, e nenhuma posição é considerada executada sem prova.
- **Sabe não operar.** O sistema tem uma "decisão do ciclo" visível no painel: OPERAR, AGUARDAR, BLOQUEAR
  ou SUSPENDER — esperar também é uma decisão certa, e ele te mostra o motivo em português.
- **Nunca decide sozinho sobre dinheiro real.** Toda promoção automática de estratégia só vale para
  simulação (PAPER).

**Estado geral:** tudo o que era possível fazer sem chaves reais e sem servidor externo **está feito e
testado**. O que falta depende de você (chaves de corretoras/IA, servidor Oracle, celular para testar o
aplicativo móvel) — a lista está no final.

## 2. O que já funciona (e foi provado no aplicativo instalado)

1. **Instalar e atualizar sem perder dados** — o instalador é testado de ponta a ponta, inclusive um
   upgrade real da versão antiga (0.4.0) para a atual (0.5.0), com backup automático verificado.
2. **Entrar no painel com segurança** — seu login único (só existe o dono), senha protegida, assistente de
   primeira execução e código de recuperação.
3. **Ver os 10 traders** — 3 certificados operando em simulação e 7 em treinamento (SHADOW), cada um com
   sua própria sala, gráfico e histórico.
4. **Rodar ciclos em simulação** — provado no aplicativo instalado: 20 propostas, 2 entradas, livro-caixa
   confere, e ao reiniciar o programa tudo volta do disco.
5. **Copiloto Manual** — você registra uma operação que fez na corretora e o programa acompanha, avisa e
   guarda o resultado. Ele **nunca** envia ou cancela ordem em corretora.
6. **Alertas** — notificações do Windows funcionam de verdade; Telegram e Discord estão prontos (faltam suas
   credenciais); o centro de alertas guarda o histórico mesmo se um aviso não chegar.
7. **Gráficos e indicadores honestos** — inclui o que nunca é inventado: quando um número não pode ser
   calculado com honestidade (por exemplo, volatilidade com poucas operações), ele mostra "indefinido" com o
   motivo — nunca um número falso.
8. **Segurança das chaves** — suas chaves de API ficam criptografadas no Windows (DPAPI), nunca em texto
   claro, nunca aparecem em mensagens ou backups.
9. **Celular (PWA)** — o painel se adapta a telas pequenas (testado até 320 px) e o mecanismo de
   notificação push está pronto; para receber no seu celular falta o servidor HTTPS (a Oracle).

## 3. O que está pronto, mas ainda não foi provado "por inteiro" (e por quê)

| Assunto | O que falta e por quê |
| :--- | :--- |
| Inteligência artificial (OpenAI, Claude, etc.) | As telas e o sistema estão prontos; para testar de verdade cada provedor, faltam as chaves pagas. Nenhuma chave de mentira foi usada para fingir teste. |
| Corretoras reais (testnet) | Mesma coisa: falta a chave de leitura de cada corretora que você escolher. |
| Telegram / Discord | O programa está pronto; falta você criar o robô (bot) e colar as credenciais no painel. |
| WhatsApp | Não integrado (precisa de credenciais oficiais). |
| Oracle Cloud (servidor no ar) | O pacote e o manual estão prontos; falta criar a máquina na Oracle e sua autorização (é uma etapa irreversível). |
| Celular de verdade (Android/iPhone) | O app é responsivo e o push é real; para o teste final faltam celulares físicos + o servidor HTTPS. |
| Aviso do Windows SmartScreen no instalador | Sumiria com um certificado de assinatura de código — uma compra opcional, só importa na hora de distribuir para outras pessoas. |
| Sleep/wake (dormir/acordar o PC) | Não foi exercitado numa sessão real de suspensão — anotado como ressalva. |

Ou seja: **nada disso é um defeito do programa** — é dependência de chave, servidor ou equipamento. Está
tudo registrado para auditoria.

## 4. O que não existe (e é bom saber)

- **Operar com dinheiro real.** Bloqueado por decisão sua e trava de código. Não há ativação escondida.
- **Prometer lucro.** O programa nunca promete ganho; a lógica central é "buscar vantagem com risco
  controlado e saber quando não operar".
- **Aprender sozinho e mudar de estratégia com dinheiro real.** A evolução automática só mexe em simulação.

## 5. Como usar o programa (passo a passo)

### 5.1 Primeira vez

1. Instale pelo instalador (`DZ23 Trading AI Setup`). Se o Windows avisar sobre "editor desconhecido",
   é esperado enquanto não há assinatura de código.
2. Abra pelo atalho "DZ23 Trading AI". A primeira execução mostra o assistente: crie sua credencial e
   **guarde o código de recuperação que aparece uma única vez**.
3. No painel, siga o fluxo principal: **CONECTAR → MODO (DEMO/REAL/COPILOTO) → PLAY**.
   - REAL estará desabilitado — isso é o comportamento correto.

### 5.2 Uso diário (Modo Simples)

1. Na página **Início**, veja o estado da mesa e a **Decisão do ciclo** (OPERAR/AGUARDAR/BLOQUEAR/SUSPENDER)
   com o motivo explicado.
2. Quer detalhes? **Mesa dos 10 Traders** mostra cada trader, sua sala, gráfico e resultados.
3. **Escola Central** mostra o que está certificado, em treinamento e por quê.
4. **Alertas** mostram o que aconteceu; o sino acumula o histórico.

### 5.3 Copiloto Manual (para operações que VOCÊ faz na corretora)

1. Abra **Copiloto Manual** e escolha o ativo. O sistema mostra uma análise com fonte e horário.
2. **Custos nunca são estimados pelo sistema** — só entram os valores reais que você informar.
3. Fez a operação na corretora? Digite o preço real, quantidade, taxas e horário e salve.
4. Clique em **"Acompanhar esta operação"**. O sistema monitora, atualiza e avisa conforme regras objetivas
   (stop, alvo etc.).
5. No fim, registre a saída (parcial ou total). O resultado vai para o histórico e alimenta o aprendizado
   — **o sistema continua sem poder tocar na sua corretora.**

### 5.4 Se algo der errado

- **Esqueci a senha:** use o código de recuperação (tela de login).
- **Quero voltar uma atualização:** o instalador cria backup verificado antes de atualizar; o procedimento
  de rollback está em `docs/mission/ROLLBACK.md`.
- **Programa não abre:** verifique o arquivo de log `launcher.log` na pasta do aplicativo; o programa avisa
  por escrito o que aconteceu em vez de falhar em silêncio.

## 6. Segurança do seu dinheiro (leia com atenção)

- **Você NÃO autorizou operações com dinheiro real.** O sistema respeita isso em várias camadas.
- **R$ 1.000** estão registrados apenas como *possível capital inicial para um piloto futuro*. Isso **não é**
  um depósito, **não é** uma autorização, **não é** uma meta. Nada será iniciado — nem com R$ 1 — sem você
  autorizar após a auditoria independente (registro formal em `REGISTRO-CAPITAL-PILOTO.md`).
- O executor (Qoder) **não pode** e **não** declara o sistema "financeiramente aprovado". Essa aprovação
  será feita por auditoria independente (ChatGPT + Claude), como você definiu.

## 7. O que eu (proprietário) preciso decidir/fornecer quando quiser avançar

1. **Chaves de IA** (uma ou mais) para testar os provedores de verdade.
2. **Chave de corretora** (com permissão mínima, só leitura) para o teste de conexão real.
3. **Telegram/Discord**: criar o bot/webhook e informar no painel (opcional).
4. **Oracle**: criar a máquina ARM64 e autorizar a publicação (etapa irreversível — só quando você quiser).
5. **Publicação da versão (tag/Release no GitHub):** autorização explícita sua.
6. **Certificado de assinatura de código** (opcional, para distribuição).

## 8. Onde estão as provas (se você quiser conferir)

- `docs/mission/WINDOWS-RELEASE-EVIDENCE.md` — todos os testes feitos no aplicativo instalado, um por um.
- `docs/mission/evidence/` — fotos, logs e relatórios de cada prova.
- `docs/mission/entrega-final/MATRIZ-REQUISITOS-V8.md` — a lista completa do que foi pedido × o que foi
  feito × onde está a prova.
- `docs/mission/entrega-final/RELATORIO-HOMOLOGACAO-V8.md` — o relatório técnico de homologação.

## 9. Palavras que aparecem no painel, explicadas

| Palavra | Significado simples |
| :--- | :--- |
| PAPER | Simulação com dinheiro de ficção. Nada real acontece. |
| TESTNET | Rede de teste de uma corretora. Dinheiro de teste. |
| LIVE / REAL | Dinheiro de verdade. **Bloqueado.** |
| SHADOW | Trader em treinamento: analisa, mas não opera. |
| Ledger | Livro-caixa interno que confere cada centavo simulado. |
| Abstenção | Decisão de não operar. Pode ser a decisão certa. |
| Drawdown | Quanto o resultado caiu do ponto mais alto até o mais baixo. |
| Sharpe/Sortino | Medidas de retorno por risco. Aparecem só quando fazem sentido (com operações suficientes); senão, "indefinido" com motivo. |
| AGUARDAR/BLOQUEAR/SUSPENDER | Estados da decisão do ciclo: esperar / não pode agora (motivo) / parar por segurança. |
