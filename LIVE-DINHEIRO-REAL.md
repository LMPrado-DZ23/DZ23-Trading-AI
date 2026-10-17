# Operar com DINHEIRO REAL — leia antes de ligar

O caminho auditável para preparar dinheiro real existe. Ele **ainda não está
liberado para operar** e não liga sozinho: além das ações do proprietário, há
provas externas, controles operacionais e um selo de código que o painel não
concede. O estado real de cada classe aparece na mesma tela.

## Estado seguro desta branch

Esta branch prepara a sala real, mas **não autoriza operação LIVE**. Quando um
agente conclui a Escola Central, ele entra automaticamente na fila
`aguardando_configuracao_do_proprietario`. Esse encaminhamento não contém
capital, limites nem autorização para enviar ordens.

O proprietário ainda precisa configurar o piloto por estratégia, versão e
conta. Depois disso, a autorização do piloto é uma ação explícita e separada,
com nova validação da certificação e da prova da conta REAL no instante da
autorização. Mesmo um piloto
autorizado continua sujeito ao selo global `MESA_LIVE_LIBERADA=False` e às
demais fechaduras descritas abaixo.

### Evidência histórica fora da amostra

O validador público agora busca histórico paginado, recusa séries incompletas,
descarta a vela em formação e avalia blocos fora da amostra não sobrepostos,
separados do treino por embargo. Toda posição que ainda estiver aberta no fim
de um bloco é marcada a mercado e paga os custos de ida e volta; ela não pode
simplesmente desaparecer do resultado. Uma estratégia só recebe aprovação
histórica quando tem pelo menos 20 operações, resultado líquido positivo, sem
ressalva de custos e com pelo menos 60% das janelas positivas.

A rodada de referência em dados públicos da Binance, com 5.000 velas de 1h,
treino de 2.000, embargo de 48 e cinco testes de 500 velas, encontrou apenas
dois candidatos aprovados: `trend_following` em ETH/USDT (+8,31% líquidos em
27 operações) e `momentum` em ETH/USDT (+12,02% em 23 operações). Todas as
estratégias em BTC/USDT e as outras oito em ETH/USDT foram reprovadas. A
evidência completa, com hash da série e revisão do código, está em
`docs/mission/evidence/real-readiness/binance-btc-eth-1h-oos.json`.

Essa aprovação é somente histórica. Ela **não forma o agente**, não substitui
PAPER prolongado, fill TESTNET reconciliado nem a prova somente leitura da
conta REAL, e não abre nenhuma trava LIVE. A Escola continua sendo a autoridade
de formação; quando formar o agente, o encaminhamento automático o coloca na
Sala REAL bloqueada para decisão explícita do proprietário.

Para repetir a rodada:

```powershell
uv run python scripts/validar_estrategias.py --exchange binance `
  --instrumentos BTC/USDT,ETH/USDT --timeframe 1h --velas 5000 `
  --treino 2000 --teste 500 --embargo 48 --capital 1000 `
  --saida-json docs/mission/evidence/real-readiness/binance-btc-eth-1h-oos.json
```

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
| IQ Option | 7 MCPs oficiais validados | prova de conta preparada; ainda não executada | token MCP oficial no painel cifrado ou ambiente |

As quatro corretoras sem sandbox ficam disponíveis para leitura pública,
catálogo de capacidades e preparação da conta. O sistema recusa apresentá-las
como TESTNET operacional. Chaves, segredos, passphrases e UID são armazenados
pelos mesmos controles de segredo do painel e nunca devem ser copiados para
documentação, commits ou tickets.

As nove corretoras CCXT também possuem adaptador de sala da Escola para
mercado spot; Binance, Bybit e OKX mantêm ainda o mercado de futuros já
declarado. Isso permite formar e encaminhar agentes por corretora/conta sem
inventar capacidade de TESTNET nas quatro que não oferecem sandbox nativo.

O checklist de execução valida os campos mínimos por corretora antes de
mostrar as credenciais como prontas. OKX, Coinbase Exchange, KuCoin e Bitget
exigem também a passphrase; somente chave e segredo permanecem bloqueados
nessas quatro integrações.

A IQ Option usa uma integração MCP oficial separada do CCXT, com sete
servidores por classe de instrumento. Os sete endpoints estão configurados no
Codex usando apenas a referência `IQ_OPTION_MCP_BEARER_TOKEN`, sem token
embutido. Em 2026-10-16, uma credencial fornecida localmente pelo proprietário
provou os sete handshakes. O catálogo publica
`OFFICIAL_MCP_READ_ONLY_PREPARED`: a sonda passiva continua executando somente
`initialize` e `tools/list`, e um módulo separado prepara uma única consulta
autenticada literal, `list_balances` com filtro `NORMAL`. Antes dessa consulta,
o catálogo remoto e as marcações `readOnly`/`destructive=false` são
revalidados; qualquer drift ou ferramenta mutável fecha o caminho **antes** de
`call_tool`. O resultado compara apenas o
`balance_id` informado pelo proprietário e não devolve nem persiste saldo,
moeda ou ID bruto. Essa primeira consulta de conta ainda não foi executada
nesta branch e exige confirmação no momento da ação. Não existe ferramenta de
ordem no módulo de leitura, não há sandbox homologado e nada disso autoriza
execução. O token pode ficar cifrado pelo painel ou apenas no ambiente e nunca
é reutilizado como credencial de execução pelo DZ23.

A integração oficial também permite que uma estratégia IQ Option formada em
sua sala PAPER entre na fila bloqueada da Sala REAL. A IQ Option continua
marcada como laboratório, não cria sala LIVE, não possui executor de ordens
nesta camada e ainda exige a prova autenticada somente leitura da conta antes
de qualquer pedido de piloto para conta REAL. Ao selecionar IQ Option no
painel, o proprietário precisa declarar o payout observado, entre 0 e 1. O
boot então cria a sala isolada `opcoes_binarias|paper`; payout ausente, não
finito, zero, negativo ou acima de 1 falha fechado e fica visível no status da
Escola. O sistema não presume payout e nunca transforma esse campo em permissão
de execução.

O painel oficial da IQ Option foi verificado em 2026-10-16: `Read access` é
sempre habilitado e os acessos de negociação de Options e Margin Products são
selecionáveis. Em 2026-10-17, o proprietário autorizou e criou o token
`DZ23 Read Only` com **Read access ligado e todos os Trading access
desligados**, preservando os dois tokens anteriores. A sonda passiva no HEAD
`99025cb` confirmou os sete endpoints, 60 ferramentas somente leitura, zero
mutáveis, zero desconhecidas, zero ausentes e zero chamáveis pelo caminho de
metadados. O token ficou cifrado na configuração local. Essa prova fecha o
gate de permissão mínima do token, mas não substitui a leitura autenticada e a
confirmação da identidade da conta.

Depois de autorizar a conexão e carregar o token somente no ambiente local, a
prova reproduzível pode ser gerada sem credencial na linha de comando:

```powershell
$env:IQ_OPTION_MCP_BEARER_TOKEN = Read-Host -MaskInput
uv run python scripts/verify_iq_option_mcp.py `
  --output .\data\evidence\iq-option-mcp.json
Remove-Item Env:IQ_OPTION_MCP_BEARER_TOKEN
```

O comando retorna sucesso apenas quando os sete endpoints completam o
handshake e `tools/list`, com zero ferramenta chamável. A evidência omite
token, mensagens remotas e metadados das ferramentas; qualquer contradição,
credencial ausente, handshake parcial ou drift do contrato esperado falha
fechado. O contrato aceita tanto o catálogo antigo misto — 60 ferramentas de
leitura e 32 mutáveis, todas bloqueadas — quanto o catálogo estritamente
read-only. O token `DZ23 Read Only` produziu somente o segundo: 60 de leitura e
zero mutáveis. Nenhuma ferramenta foi chamada nessa prova e nenhuma ordem foi
enviada.

As credenciais de execução também ficam vinculadas à corretora escolhida.
Trocar o seletor não reaproveita chave, segredo ou passphrase de outro
provedor: o checklist, o teste de conexão e a construção da mesa permanecem
bloqueados até que uma nova chave e um novo segredo sejam informados juntos.

O botão **Testar leitura pública** consulta ticker e três velas de um mercado
conhecido usando o adaptador CCXT da corretora, sem carregar chave e sem possuir
caminho de ordem. A rodada de referência de 2026-10-16 confirmou leitura
pública nas nove corretoras (9/9). O resultado auditável fica em
`docs/mission/evidence/real-readiness/corretoras-publicas.json`. Essa prova
confirma dados públicos e conectividade; não substitui autenticação somente
leitura da conta, fill TESTNET nem homologação LIVE.

Para repetir a prova das nove corretoras:

```powershell
uv run python scripts/verificar_corretoras_publicas.py `
  --saida-json docs/mission/evidence/real-readiness/corretoras-publicas.json
```

### Prova da conta REAL sem habilitar execução

A credencial da conta REAL é separada da credencial TESTNET e usa os campos
`REAL_READ_ONLY_API_KEY`, `REAL_READ_ONLY_API_SECRET`,
`REAL_READ_ONLY_API_PASSWORD` e `REAL_READ_ONLY_API_UID`. O DZ23 também grava
`REAL_READ_ONLY_EXCHANGE`, que vincula esse conjunto à corretora selecionada.
Ela não é lida pelo adaptador que envia ordens. Ao trocar de corretora, o
painel exige nova chave e novo segredo antes de qualquer consulta, evitando
enviar por engano a credencial de uma corretora para outra.

No painel, em **Configuração → Conta REAL — somente leitura**:

1. crie na corretora uma chave sem permissão de trade e sem movimentação de
   fundos;
2. cole a credencial no painel local e salve;
3. clique em **Comprovar conta REAL somente leitura**;
4. informe um apelido local. Nas corretoras CCXT, informe separadamente o
   identificador real exibido pela corretora. Na IQ Option, confirme que o
   token representa uma única conta REAL regular; a prova falha fechado se
   `list_balances(NORMAL)` devolver zero ou mais de uma conta;
5. confirme as duas restrições de permissão.

O servidor executa somente consultas autenticadas de saldo, ordens abertas e
contas. O identificador informado é comparado em memória com o identificador
devolvido pela corretora; adaptadores sem identidade verificável permanecem
bloqueados. Nenhum saldo, ordem ou identificador privado é devolvido ao painel
ou salvo. A prova durável contém apenas corretora, rótulo local, horário, nomes
dos métodos de leitura, hash da credencial e uma impressão HMAC da identidade.
Trocar qualquer parte da credencial invalida a prova automaticamente.

Na IQ Option, o painel oficial não precisa expor o `balance_id`: mediante a
confirmação explícita acima, o DZ23 aceita somente uma resposta com exatamente
uma conta `regular`, usa o identificador apenas em memória para gerar a
impressão e o descarta. Se aparecer uma segunda conta, a renovação é recusada
até o proprietário escolher um fluxo de identificação inequívoco.

A prova vale por 24 horas. Depois desse prazo, ou se o relógio registrado for
inválido, o checklist volta a bloquear a conta e exige uma nova consulta
autenticada. O painel mostra quando a prova expira; renovar a prova continua
usando somente métodos de leitura e não envia ordens.

### Credencial de execução REAL separada

A futura execução LIVE não reutiliza a credencial TESTNET nem a chave somente
leitura. Ela possui namespace próprio: `LIVE_EXCHANGE_API_KEY`,
`LIVE_EXCHANGE_API_SECRET`, `LIVE_EXCHANGE_API_PASSWORD` e
`LIVE_EXCHANGE_API_UID`, vinculados à corretora por
`LIVE_EXECUTION_EXCHANGE`. O painel recusa preparar a credencial se sua
impressão for igual à TESTNET ou à chave somente leitura.

Em **Configuração → Conta REAL — credencial de execução futura**, o
proprietário salva essa terceira credencial e confirma que ela possui trade,
mas não saque ou movimentação de fundos. A comprovação constrói somente a
sonda isolada de leitura: consulta saldo, ordens abertas e identidade de conta,
sem construir o executor e sem chamar `create_order`. O mesmo ID bruto já
confirmado pela chave somente leitura é comparado em memória; apenas hashes
vinculados a cada credencial são persistidos.

A prova de execução também vale 24 horas. Rotacionar qualquer uma das duas
credenciais reais, mudar a corretora, expirar a prova de leitura ou trocar a
identidade da conta invalida a prova e a ativação persistida. Em um futuro
reinício LIVE, a mesa revalida tudo e projeta a credencial `LIVE_*` para o
cliente CCXT apenas em memória. `EXCHANGE_API_*` continua sendo exclusivamente
a credencial de homologação TESTNET.

Esta preparação **não libera LIVE**. Nesta branch,
`MESA_LIVE_LIBERADA=False`; nenhuma ordem é enviada e a ativação final continua
dependendo de mudança de código auditada e de novo ato explícito do
proprietário.

A homologação TESTNET também é vinculada ao conjunto exato de chave, segredo,
passphrase e UID que produziu o fill reconciliado. Qualquer rotação invalida a
prova anterior e exige novo fill seguido de reconciliação. Horários futuros ou
sem fuso e identidades de ordem malformadas nunca satisfazem o checklist.

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

A avaliação da Escola acontece automaticamente depois de cada ciclo persistido
da mesa. Portanto, a passagem para essa fila não depende de abrir o painel nem
de chamar manualmente a rota de avaliação. Se a avaliação falhar, a falha fica
visível no estado da mesa, o ciclo financeiro não ganha permissão adicional e
nenhum agente é liberado. Se já existir uma autorização válida para o mesmo
agente e contexto, o encaminhamento automático preserva esse estado em vez de
fazê-lo regredir; autorizações encerradas ou de outro contexto não são
reaproveitadas.

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
a prova de leitura expirar, os limites deixarem de ser compatíveis ou a
reconciliação ficar pendente, a prontidão volta a ficar bloqueada
automaticamente. O vigia LIVE também recusa continuamente qualquer piloto sem
vínculo explícito com conta REAL ou cuja prova não corresponda mais à mesma
corretora e conta. A impressão da identidade verificada é copiada para o
registro interno do pedido: refazer a prova com outra conta e manter o mesmo
apelido não redireciona um piloto já autorizado. Essa impressão não é exposta
pelas respostas públicas do piloto.

## Antes de tudo
Perdas com dinheiro real são **suas e irreversíveis**. Nenhum resultado de
teste, backtest ou paper prevê lucro futuro. Ligue com dinheiro que você
pode perder inteiro.

Se você opera **para terceiros** (dinheiro de clientes), isso é atividade
regulada no Brasil — o software não muda a sua obrigação legal. Cada cliente
deve operar a própria conta, com as próprias chaves.

## As cinco fechaduras
1. **Frase exata no pedido de ativação**:
   `EU AUTORIZO OPERAR COM DINHEIRO REAL`
   Qualquer variação (minúscula, sem acento, parecida) **não** liga. A frase
   não é aceita por `/v1/config`, não é salva no arquivo de configuração e só
   é projetada em memória para o broker depois que a ativação vinculada ao
   agente e à conta foi validada.
2. **Teto por ordem** (`LIVE_MAX_ORDER_VALUE`): valor máximo de UMA ordem.
   Sem ele a ponte recusa nascer — um erro de cálculo viraria ordem do
   tamanho da conta.
3. **Perda máxima diária** (`LIVE_MAX_DAILY_LOSS`): ao atingir, os envios
   param até você revisar.
4. **Modo da corretora** em `live` e chaves reais configuradas. Durante a
   preparação ele permanece em PAPER/TESTNET; a mudança para `live` pertence
   à mesma etapa final auditada que abre o selo, nunca a um preflight.
5. **Selo de código da mesa**: a mesa de agentes recusa construir corretora
   em LIVE sem autorização explícita de código (`live_authorized`), que o
   painel **não** concede e a configuração salva **não** substitui
   (auditoria FIN-1, ADR-006). No estado atual do produto este selo está
   fechado: mesmo com os itens 1–4 verdes, a mesa recusa arrancar em LIVE e
   diz o motivo. Ele só abre por decisão do dono em código auditado.

Os limites financeiros ficam em **Configuração → Limites globais para
piloto REAL**. O painel não sugere valores: o proprietário informa exposição
global, teto por ordem e perda máxima diária. O servidor recusa a gravação
inteira se qualquer valor for vazio, zero, negativo, não numérico, infinito
ou `NaN`, e também recusa teto por ordem maior que a exposição global. Salvar
esses limites não autoriza piloto, não muda o modo da mesa e não abre LIVE.

Da mesma forma, a configuração do piloto formado não possui valores
preenchidos automaticamente. Capital, perdas e prazo precisam ser digitados
pelo proprietário antes do pedido de autorização.

O painel mostra esse checklist em **Mesa de Agentes → "Dinheiro real: posso?"**
com a ação exata de cada item que falta.

O endpoint autenticado `GET /v1/live/status` também publica um contrato
canônico `estado_de_liberacao` (`dz23.liberacao-real/1`). Ele separa as
pendências em `acao_do_proprietario`, `prova_externa`,
`controle_operacional` e `liberacao_de_codigo`. Seus estados são:

- `preparacao_pendente`: existe ao menos um pré-requisito além do selo;
- `aguardando_liberacao_de_codigo`: todos os demais itens foram comprovados,
  mas o modo continua seguro em PAPER/TESTNET e o selo auditado fechado;
- `liberado`: só pode aparecer quando nenhum item estiver pendente.

O mesmo payload traz `sala_real`, com contagens por estado dos
encaminhamentos e pilotos. Esse retrato nunca contém credenciais e declara
explicitamente `envio_real_habilitado=false` nesta branch. Se a leitura da
Sala REAL falhar, o resumo fica indisponível e a prontidão continua fechada.

Para um preflight reproduzível da instalação em execução, use a rota agregada
autenticada e o verificador somente leitura:

```powershell
$env:ACCESS_PASSWORD = Read-Host -AsSecureString | ConvertFrom-SecureString -AsPlainText
python scripts/real_readiness_check.py `
  --base-url http://127.0.0.1:8000 `
  --target preparacao `
  --output .\data\evidence\real-readiness.json
Remove-Item Env:ACCESS_PASSWORD
```

O script nunca aceita o token como argumento e não o inclui no relatório. O
alvo `preparacao` exige todos os pré-requisitos sem abrir o selo; o alvo
`live` exige, adicionalmente, que o selo, o envio e o checklist estejam todos
liberados. Ambos retornam código diferente de zero quando o contrato está
incompleto, indisponível ou contraditório.

O checklist também exige um ensaio local de recuperação recente. Depois de
gerar o backup pelo painel, execute no host:

```powershell
python scripts/verify_local_restore.py `
  --data-dir .\data `
  --archive .\data\backups\dz23-backup-AAAAMMDD-HHMMSS.tar.gz
```

O ensaio restaura em área temporária, valida hashes, SQLite, JSON e JSONL e
grava uma prova válida por 30 dias. Não inicia a mesa e não envia ordens. A
prova local não cobre PostgreSQL externo; quando `DATABASE_URL` estiver ativo,
o backup lógico e o restore desse banco continuam sendo um gate separado do
ambiente de destino.

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
