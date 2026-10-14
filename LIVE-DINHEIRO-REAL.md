# Operar com DINHEIRO REAL — leia antes de ligar

O caminho para dinheiro real existe e está pronto. Ele **não liga sozinho**:
são cinco fechaduras independentes — quatro na sua mão e um selo de código
que o painel não concede (você vê o estado real de todas elas na mesma tela).

## Antes de tudo
Perdas com dinheiro real são **suas e irreversíveis**. Nenhum resultado de
teste, backtest ou paper prevê lucro futuro. Ligue com dinheiro que você
pode perder inteiro.

Se você opera **para terceiros** (dinheiro de clientes), isso é atividade
regulada no Brasil — o software não muda a sua obrigação legal. Cada cliente
deve operar a própria conta, com as próprias chaves.

## As cinco fechaduras
1. **Frase exata** em `LIVE_TRADING_CONFIRM`:
   `EU AUTORIZO OPERAR COM DINHEIRO REAL`
   Qualquer variação (minúscula, sem acento, parecida) **não** liga.
2. **Teto por ordem** (`LIVE_MAX_ORDER_VALUE`): valor máximo de UMA ordem.
   Sem ele a ponte recusa nascer — um erro de cálculo viraria ordem do
   tamanho da conta.
3. **Perda máxima diária** (`LIVE_MAX_DAILY_LOSS`): ao atingir, os envios
   param até você revisar.
4. **Modo da corretora** em `live` e chaves reais configuradas.
5. **Selo de código da mesa**: a mesa de agentes recusa construir corretora
   em LIVE sem autorização explícita de código (`live_authorized`), que o
   painel **não** concede e a configuração salva **não** substitui
   (auditoria FIN-1, ADR-006). No estado atual do produto este selo está
   fechado: mesmo com os itens 1–4 verdes, a mesa recusa arrancar em LIVE e
   diz o motivo. Ele só abre por decisão do dono em código auditado.

O painel mostra esse checklist em **Mesa de Agentes → "Dinheiro real: posso?"**
com a ação exata de cada item que falta.

## Ordem recomendada (não pule)
1. Rode em **testnet** até ver uma ordem preenchida e reconciliada.
2. Confira a contabilidade fechando (patrimônio e atribuição por trader).
3. Comece com teto **baixo** — por exemplo, 1% do que você imagina.
4. Acompanhe o primeiro dia inteiro antes de aumentar qualquer limite.

## Parada de emergência
Botão vermelho **PARADA DE EMERGÊNCIA** no painel:
- bloqueia imediatamente qualquer envio de ordem deste inquilino;
- para o laço autônomo e as entradas novas;
- **não fecha posições sozinho** — posição aberta continua aberta, sob o
  guardião. Fechar é decisão sua, consciente.

Para voltar a operar, é preciso repetir a frase de autorização. Não é um
clique distraído.

## O que o sistema garante (e o que não garante)
Garante: nenhuma ordem real sai sem as quatro fechaduras; nenhuma ordem sai
sem preço de referência conhecido; nenhuma ordem passa do teto; envio com
timeout vira "incerto" e é reconciliado em vez de reenviado; tudo fica no
journal com segredos redigidos.

Não garante: lucro, acerto das estratégias, disponibilidade da corretora,
nem que o mercado se comporte como no teste.
