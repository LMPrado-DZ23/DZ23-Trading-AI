# Operar com DINHEIRO REAL — leia antes de ligar

O caminho para dinheiro real existe e está pronto. Ele **não liga sozinho**:
são cinco fechaduras independentes — quatro na sua mão e um selo de código
que o painel não concede (você vê o estado real de todas elas na mesma tela).

## Estado seguro desta branch

Esta branch prepara a sala real, mas **não autoriza operação LIVE**. Quando um
agente conclui a Escola Central, ele entra automaticamente na fila
`aguardando_configuracao_do_proprietario`. Esse encaminhamento não contém
capital, limites nem autorização para enviar ordens.

O proprietário ainda precisa configurar o piloto por estratégia, versão e
conta. Depois disso, a autorização do piloto é uma ação explícita e separada,
com nova validação da certificação no instante da autorização. Mesmo um piloto
autorizado continua sujeito ao selo global `MESA_LIVE_LIBERADA=False` e às
demais fechaduras descritas abaixo.

### Corretoras disponíveis

| Corretora | Dados públicos | TESTNET pelo CCXT instalado | Credencial adicional |
|---|---:|---:|---|
| Binance | sim | sim | — |
| Bybit | sim | sim | — |
| OKX | sim | sim | passphrase |
| Coinbase Exchange | sim | sim | passphrase |
| Gate.io | sim | sim | — |
| Kraken | sim | não há sandbox nativo | — |
| KuCoin | sim | não há sandbox nativo | passphrase |
| Mercado Bitcoin | sim | não há sandbox nativo | — |
| Bitget | sim | não há sandbox nativo | passphrase |

As quatro corretoras sem sandbox ficam disponíveis para leitura pública,
catálogo de capacidades e preparação da conta. O sistema recusa apresentá-las
como TESTNET operacional. Chaves, segredos, passphrases e UID são armazenados
pelos mesmos controles de segredo do painel e nunca devem ser copiados para
documentação, commits ou tickets.

### Prova da conta REAL sem habilitar execução

A credencial da conta REAL é separada da credencial TESTNET e usa os campos
`REAL_READ_ONLY_API_KEY`, `REAL_READ_ONLY_API_SECRET`,
`REAL_READ_ONLY_API_PASSWORD` e `REAL_READ_ONLY_API_UID`. Ela não é lida pelo
adaptador que envia ordens.

No painel, em **Configuração → Conta REAL — somente leitura**:

1. crie na corretora uma chave sem permissão de trade e sem movimentação de
   fundos;
2. cole a credencial no painel local e salve;
3. clique em **Comprovar conta REAL somente leitura**;
4. identifique a conta e confirme as duas restrições de permissão.

O servidor executa somente consultas autenticadas de saldo, ordens abertas e,
quando o adaptador oferece, contas. Nenhum saldo, ordem ou identificador
privado é devolvido ao painel. A prova durável contém apenas corretora, rótulo
da conta, horário, nomes dos métodos de leitura e um hash da credencial. Trocar
qualquer parte da credencial invalida a prova automaticamente.

Uma consulta bem-sucedida comprova acesso de leitura; não comprova, sozinha,
que a corretora retirou permissões de trade ou movimentação. Essa restrição é
declarada pelo proprietário no DZ23 e deve ser conferida também no painel da
corretora.

Mesmo com a prova válida, o checklist exige um piloto formado, configurado e
explicitamente autorizado para a mesma corretora e conta. Se o modo LIVE vier
a ser liberado em código no futuro, todo agente sem essa autorização será
bloqueado pelo vigia fail-closed.

### Da Escola para a conta REAL

Quando um agente se forma, ele entra automaticamente na fila **Sala REAL
bloqueada**. O contexto que certificou a estratégia permanece preservado
(corretora, conta PAPER, mercado, ativo e horizonte), separado da conta REAL
de destino. Essa separação evita fingir que a prova ocorreu com dinheiro real.

Ao configurar os limites pelo painel, o destino só pode mudar da conta PAPER
para uma conta REAL quando:

1. a prova autenticada somente leitura dessa conta estiver válida;
2. a corretora for a mesma do contexto que formou o agente;
3. capital máximo, perda diária, perda total e prazo forem definidos;
4. o proprietário autorizar explicitamente o piloto.

O checklist REAL revalida a Escola antes de considerar a autorização vigente.
Também exige que os tetos globais caibam nos limites do piloto, que a
reconciliação financeira esteja atual e sem divergências e que a parada de
emergência esteja desarmada. Se a formatura for retirada, a credencial mudar,
os limites deixarem de ser compatíveis ou a reconciliação ficar pendente, a
prontidão volta a ficar bloqueada automaticamente.

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
   Ao concluir a reconciliação segura, o DZ23 grava uma prova durável vinculada
   à corretora. Assim, trocar a configuração de TESTNET para LIVE não apaga a
   homologação. Um fill PAPER, parcial, não reconciliado ou feito em outra
   corretora não satisfaz o checklist. Credenciais e o identificador externo da
   ordem entram na prova apenas como hash e não são devolvidos pelo painel.
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
