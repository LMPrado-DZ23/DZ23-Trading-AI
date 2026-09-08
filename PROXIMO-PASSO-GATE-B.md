# MISSÃO — GATE B, MARCO 1: Point-in-Time nas fontes de dados
# Executor: Claude Code local | Workspace: C:\TradingAgents-Handoff\claude-execucao
# Categoria: Gate B/PIT → AUDITADA pelo Codex (Protocolo C, ponto 3)

## FASE 0 — Bootstrap do baseline aprovado (uma vez)
1. Localizar TradingAgents-main.zip original (SHA-256 esperado:
   1364592cc4bb21b8a424161ebe0e9047195321b2ef89138a03d59c0ccf634e5e).
   Se o hash divergir ou o arquivo não existir: PARAR e reportar.
2. Extrair para C:\TradingAgents-Handoff\claude-execucao\TradingAgents-main
3. Sobrepor os 37 arquivos de C:\TradingAgents-Handoff\entrada\
   fase0-r12-integrated.zip (SHA-256:
   cfaa360084bfd2dbc2e609837ef1653a860c99a860f21ae96e851b9baa4473e3).
4. Criar venv, instalar dependências (pip install -e . ou requirements),
   rodar: python -m pytest tests/ -q
   GATE DE PARTIDA: exatamente 849 passed / 2 skipped / 71 subtests.
   Divergiu → PARAR e reportar (baseline corrompido).
5. git init + commit "baseline: Gate A r12 aprovado (849/2/71)".

## ESCOPO DO MARCO (implementar tudo, na ordem)
A. available_at OBRIGATÓRIO nas fontes
   - Toda construção de UntrustedEvidence a partir de fetchers (FRED, news,
     social, fundamentals, Polymarket) deve preencher available_at com o
     timestamp REAL de disponibilidade do dado (não o de fetch).
   - available_at desconhecido ⇒ marcar evidence como
     backtest_admissible=False (EXCLUSÃO no backtest — decisão selada).
B. Fundamentals por DATA DE PUBLICAÇÃO
   - Demonstrativos/indicadores só são visíveis para uma decisão em D se
     publication_date ≤ session_close(D, tz do ativo). Nada de usar o
     período fiscal como proxy de disponibilidade.
   - Fonte sem data de publicação ⇒ available_at desconhecido ⇒ regra A.
C. Polymarket fail-closed e histórico
   - Consultas devem ser point-in-time: preço/odds vigentes em ≤ decision_at.
   - Sem endpoint histórico confiável para a data ⇒ NÃO usar o dado nessa
     decisão (fail closed, jamais preço corrente como se fosse passado).
D. Testes: para A, B e C, testes que provem inclusão/exclusão nas
   fronteiras (publicado 1s antes/depois do close; available_at ausente;
   Polymarket sem histórico) + integração com is_backtest_admissible().

## NÃO FAZER
- NÃO tocar em redact.py, sanitize.py, evidence.py (núcleo), transport.py,
  backend_policy.py, gates.py — controles do Gate A estão CONGELADOS
  (exceção única: evidence.py PODE ganhar campos/validadores novos de
  available_at/admissibilidade, sem alterar sanitização/redação existentes).
- NÃO implementar sizing, backtest, PnL, broker, PAPER/SHADOW.
- NÃO adicionar credencial em código/committar .env.
- NÃO corrigir o ruff E402 de redact.py:57 (fica para marco próprio).

## VERIFICAÇÃO E ENTREGA
1. python -m pytest tests/ -q --junitxml=junit-gateB-m1.xml
   (suíte integral verde; os 849 anteriores + novos, 0 xfail).
2. Gerar MANIFEST-sha256.txt de todos os arquivos alterados/novos.
3. RUN-INFO.txt: escopo, decisões tomadas, mapa achado→teste, contagem.
4. pip freeze > pip-freeze.txt (lacuna de reprodutibilidade anotada pelo
   auditor: incluir também python --version e SO).
5. Zipar tudo (código alterado + testes + 4 arquivos acima) como
   C:\TradingAgents-Handoff\entrada\gateB-marco1-integrated.zip
   + gateB-marco1-evidence.zip; imprimir caminho, tamanho e SHA-256 de cada.
6. Apêndice no C:\TradingAgents-Handoff\HANDOFF.md com o resumo e hashes.
7. git commit por sub-bloco (A, B, C, D) com mensagens claras.

Ao final, o Prado abre o workspace no Codex para a auditoria do marco
(categoria Gate B/PIT). Nada é promovido sem o veredito independente.
