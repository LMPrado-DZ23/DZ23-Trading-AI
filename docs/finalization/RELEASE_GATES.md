# RELEASE_GATES — DZ23 Trading AI

Frase oficial: **release candidate para PAPER/SHADOW/TESTNET e preparação
REAL; LIVE continua bloqueado.**

Este documento não afirma estado de produção. Toda evidência de deployment
deve citar ambiente, SHA e horário e ser refeita no host de destino.

## Gate de código desta branch

| Gate | Prova exigida | Estado no HEAD da branch |
|---|---|---|
| suíte Python | matriz CI 3.10–3.13 e suíte local | 3.859 passaram, 5 ignorados e 71 subtestes passaram na suíte local integral; 82 testes focados da ativação final, selo, prontidão, resolvedores de conta REAL/treino e runtime IQ passaram; matriz 3.10–3.13 aguardará o push deste HEAD no PR #14 |
| lint | `ruff check .` | verde local no HEAD e no run `39027792071` do PR #14 |
| instalação limpa | job CI dedicado | verde no run `39027792071` do PR #14 |
| dependências/segredos/licenças/SBOM | jobs CI dedicados | verdes no run `39027792071` do PR #14 |
| PostgreSQL e ARM64 | jobs CI dedicados | verdes no run `39027792071` do PR #14; ARM64 valida dependências, não homologa host Oracle |
| pacote Windows | build, bundle acceptance/E2E e matriz de instalador | build e 21/21 E2E verdes no run `39020200939`; matriz completa com zero falhas no run `39020202822`; ambos no commit `b7b0965`; publicação deliberadamente omitida |
| runtime da automação | Actions oficiais sem Node 20 depreciado | `checkout@v7`, `setup-python@v7`, `setup-uv@v10.3.0`, artifacts v7/v8 e gitleaks v3 validados nos runs `39020198634` e `39020200939` |
| assinatura Windows | PFX somente no cofre, assinatura/verificação e limpeza efêmera | caminho implementado; build de branch provado como `UNSIGNED_TEST_ARTIFACT`; certificado confiável continua externo e tag `v*` falha fechada sem ele |
| LIVE fechado | testes + `/v1/operational-readiness` | `MESA_LIVE_LIBERADA=False` |
| rollback local | backup local + restore isolado + prova recente | aprovado em 2026-10-17: 7 itens, 3 bancos SQLite e 4 documentos JSON validados; `envio_de_ordens=false`; PostgreSQL externo continua condicional a `DATABASE_URL` |

Os testes adversariais desse total cobrem a sincronização persistida entre a
fila da Sala REAL e os estados autorizado/revogado/expirado/suspenso, a recusa
de limites financeiros `NaN`/infinitos, o bloqueio fail-closed de telemetria
de risco inválida, não finita ou negativa e a contenção transitória do replace
atômico no Windows. A identidade do encaminhamento também isola estratégia e
versão, com migração idempotente das chaves persistidas pelo formato anterior.
O vigia agora revalida também o intervalo anterior à autorização: formatura
retirada elimina a elegibilidade da fila ainda sem limites e suspende o pedido
já configurado. A API executa essa revalidação antes de serializar a resposta,
portanto o painel não apresenta por uma chamada o estado obsoleto.
Os limites globais exigidos pela prontidão agora podem ser informados no
painel sem valor sugerido; a API recusa a gravação inteira para zero,
negativos, texto, infinito, `NaN` ou teto por ordem acima da exposição. Os
limites individuais do piloto também não recebem defaults financeiros.
As nove corretoras CCXT do catálogo abrem salas PAPER spot isoladas na Escola.
A IQ Option permanece laboratório PAPER, mas uma estratégia formada pode
entrar na fila bloqueada da Sala REAL graças à integração MCP oficial. A rota
final consegue montar um runtime binário isolado somente depois de todos os
gates, sem enviar ordem; isso não habilita LIVE nem dispensa a prova de conta
somente leitura. Seu boot de produção abre uma sala `opcoes_binarias` somente quando o
payout observado foi declarado explicitamente no painel e validado no
intervalo `(0, 1]`; ausência e valores inválidos falham fechado, sem sala e
com motivo visível.
Nenhuma dessas provas abre o selo LIVE.

A credencial de negociação da IQ Option pode ser preparada em namespace
separado (`IQ_OPTION_MCP_LIVE_BEARER_TOKEN`). O gate comprova por `tools/list`
que o token expõe exatamente o contrato oficial de negociação do servidor
`binary-options` e autentica a mesma conta com `list_balances(NORMAL)`. Nenhuma
ferramenta mutável é chamada durante a preparação ou montagem. A execução em
conta de treinamento continua não homologada e `MESA_LIVE_LIBERADA=False`
permanece obrigatório neste release.

O planejamento canônico de opções binárias está implementado com transporte
selado e conectado apenas à rota final de ativação, ainda desconectado do loop
autônomo do serviço:
ativo aberto e único, limites de valor, payout mínimo e vencimento exatamente
oferecido são obrigatórios; a reconciliação por `position_id` preserva
`UNKNOWN` quando a janela não prova o destino. Antes de `place_trade`, o executor
exige autorização de código, frase exata, limites e WAL; PREPARED/UNKNOWN nunca
é reenviado, inclusive após restart. O transporte fixa quatro ferramentas no
servidor oficial `binary-options` e revalida catálogo/schema antes de cada
chamada. Um coordenador composto já persiste intenção, executor e lançamentos
e recupera exatamente uma vez os crashes entre ACK, `binary_open` e gravação
final, sem reenviar `place_trade`. Um contrato de agente agora exige
`call`/`put` e vencimento explícitos, sem converter `BUY`/`SELL`, e vincula a
decisão à mesma estratégia, versão, certificação, autorização, conta REAL e
impressão de identidade. O resolvedor privado entrega ao runtime apenas um
`balance_id` inteiro positivo, sem expô-lo na prova pública ou no `repr`. A
fábrica persistente compõe resolvedor, transporte, coordenador, subledger e
gateway por autorização; selo/frase inválidos bloqueiam antes da rede e o
restart de uma abertura confirmada não reenvia a ordem. A submissão exige a
fotografia atual da mesma autorização; revogação, suspensão ou substituição do
mandato bloqueiam antes de qualquer ferramenta remota.
Um subledger específico já mantém
aposta aberta e liquidação por retorno total comprovado, com vitória, perda,
empate, cancelamento e repetição pós-restart exatamente uma vez. A ativação
monta esse runtime sem chamar ferramenta mutável; desativação, reset e parada
de emergência o removem da memória. Faltam o loop de submissão autorizado, a
fonte operacional homologada que
produz a prova de extrato do retorno total, a resolução operacional do caso
ambíguo sem `position_id` e a homologação na conta de treinamento;
adaptar o livro spot por aparência continua expressamente proibido.

O primeiro gate da homologação IQ em treinamento já é fail-closed: o resolvedor
não presume o nome do saldo. Ele lê o enum de `list_balances` na mesma sessão,
exige um único filtro `TRAINING`, `PRACTICE` ou `DEMO` e uma única conta do tipo
correspondente. Isso não executa nem certifica ordem; somente torna a seleção
da conta de treino verificável antes do ato externo.

Em 2026-10-17, a sonda pública foi repetida no HEAD
`e9a357a211b1f11c9d9ac03fc979546b03521749`: as nove corretoras CCXT
responderam com ticker e velas, sem credenciais e sem ordens. Essa prova
confirma somente conectividade de mercado público. Ela não certifica conta,
saldo, permissões, testnet, reconciliação nem execução.

## Preflight local isolado

Em 2026-10-17, o preflight `--target preparacao` foi executado contra uma
instância local descartável, sem credenciais de corretora e sem chamadas de
ordem. O serviço respondeu saudável, mas o preflight recusou prontidão com
`preparacao_pendente`, `selo_de_codigo_live=false`,
`envio_real_habilitado=false` e 14 pendências classificadas. O código de saída
2 é a prova fail-closed esperada; não é falha do release check. No mesmo estado,
`scripts/release_check.py` aprovou arquivos, defaults seguros, isolamento de
serviços e dependência PostgreSQL. Essa execução não homologa produção nem
substitui as provas externas abaixo.

## Gate do ambiente de destino

Todos os itens abaixo precisam de evidência nova no SHA implantado:

1. TLS/reverse proxy, DNS e acesso administrativo controlado;
2. volume persistente e reinício sem perda;
3. backup local criado, inspecionado e restaurado em diretório isolado;
4. backup lógico e restore do PostgreSQL, quando `DATABASE_URL` estiver ativo;
5. `/healthz`, `/readyz`, `/metrics` autenticado e logs sem segredos;
6. coleta de métricas e alertas com janela prolongada;
7. kill switch e reconciliação comprovados;
8. SHA implantado igual ao SHA aprovado;
9. preflight `--target preparacao` concluído;
10. nenhum 5xx novo e nenhuma ordem real enviada.

No host local atual, o item 3 foi concluído por restauração em diretório
temporário. Os itens 5 e 6 ainda não podem ser certificados como ambiente de
destino: o serviço observado está em HTTP local e não há Telegram, Discord,
WhatsApp ou WebPush configurado para comprovar entrega externa. A prova não
deve ser sintetizada; o verificador exige origem HTTPS, bearer, canal externo
e janela contínua de pelo menos uma hora.

## Gate externo por corretora/conta

- credencial somente leitura criada e identidade da conta comprovada;
- fill TESTNET reconciliado, quando a corretora oferece sandbox compatível;
- PAPER prolongado por agente/estratégia/conta;
- limites do piloto e tetos globais compatíveis;
- IQ Option com os sete handshakes passivos comprovados novamente em
  2026-10-17 no HEAD `99025cb`: o token `DZ23 Read Only` anunciou 60
  ferramentas somente leitura, zero mutáveis, zero desconhecidas, zero
  ausentes e zero chamáveis; `Read access` ficou ligado e todos os nove
  controles de `Trading access` desligados. Em 2026-10-17, a chamada
  autenticada `list_balances(NORMAL)` encontrou exatamente uma conta regular
  e confirmou sua identidade somente em memória. A prova sanitizada local está
  válida, nenhum identificador bruto foi persistido e nenhuma ordem foi
  enviada. Essa prova expira em 24 horas e não substitui homologação TESTNET,
  credencial de execução separada ou certificação de estratégia/conta;
- divergências e provas expiradas ausentes.

## Ações que continuam exigindo o proprietário no momento da ação

Merge definitivo, deploy de produção, DNS, migração destrutiva, rotação de
credencial crítica, ativação LIVE e qualquer ordem/dinheiro real. Preparar o
software não concede essa autorização. A frase de confirmação não é aceita em
`/v1/config`, não é persistida nem restaurada por backup; ela é validada apenas
no pedido final vinculado ao agente e à conta. O painel só apresenta essa ação
final quando `/v1/live/status` informa `ativacao_final.disponivel=true`; em
qualquer outro estado, a lista de agentes ativáveis e o botão ficam ausentes.
Mesmo nessa etapa, o proprietário precisa digitar a conta e a frase exatas e
confirmar novamente. A ação prepara a mesa com reconciliação obrigatória e não
envia ordem.

## Condição de liberação futura

Mesmo com todos os gates anteriores verdes, o estado esperado é
`aguardando_liberacao_de_codigo`. Abrir o selo é uma alteração de código
separada, revisada e confirmada pelo proprietário no momento da ação. Esta
branch não faz essa alteração. O modo da corretora também permanece em
PAPER/TESTNET durante a preparação e só muda para LIVE junto dessa etapa final.
