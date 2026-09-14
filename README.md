# DZ23 Trading Agency

Agência autônoma de agentes de trading, operável pelo navegador e vendável
como serviço: vários traders com estratégias distintas trabalham em ciclos,
disputam capital sob um risco determinístico, executam, reconciliam e
prestam contas — enquanto o dono acompanha tudo numa mesa visual.

> **Aviso:** software experimental de pesquisa e engenharia. Não é
> recomendação de investimento, não garante lucro e pode causar perdas.
> O padrão é **paper/testnet**. Operar com **dinheiro real** é possível e
> exige quatro fechaduras explícitas do dono — leia
> [LIVE-DINHEIRO-REAL.md](LIVE-DINHEIRO-REAL.md) antes de ligar.

## O que o sistema faz

**Mesa autônoma (multiagente)**
- Três traders com estratégias realmente distintas — tendência, reversão à
  média e rompimento —, cada um com regime de elegibilidade, invalidação da
  tese e razão explícita quando decide **não** operar.
- Árbitro de carteira: veta direção oposta no mesmo ativo e resolve disputa
  por instrumento; um dono operacional por conta/instrumento.
- Risco determinístico com poder de veto e **reserva atômica** — o mesmo
  capital nunca é prometido duas vezes.
- Executor único até a corretora, com máquina de 11 estados, chave
  idempotente presa ao conteúdo do comando e **reconciliação** pós-timeout
  (sem reenvio cego, sem liberar reserva enquanto houver efeito possível).
- Contabilidade em partidas dobradas, com resultado atribuído por trader e
  correção só por compensação.
- Agendador persistente: roda sozinho, sobrevive a reinício e **reconcilia
  antes de retomar**; sinal vencido nunca vira ordem atrasada.

**Plataforma**
- Multi-tenant com papéis (viewer, analyst, operator, risk manager, admin,
  auditor) e isolamento testado entre inquilinos.
- Cadastro self-service com período de teste; login por e-mail/senha ou
  social (Google e Microsoft, OAuth com PKCE).
- Cobrança por **PIX (Woovi)**: webhook verificado por assinatura RSA sobre
  o corpo bruto — corpo adulterado ou sem assinatura não ativa nada.
- Painel em português: Command Center, Configuração, Análises, **Mesa de
  Agentes**, **Trading Floor** isométrica, Risco, Plano e pagamento, Admin.
- Backup e restauração com verificação SHA-256 por arquivo.

**Segurança herdada de auditoria adversarial**
- Núcleo aprovado em 12 rodadas de auditoria independente (redação de
  segredos fail-closed, política de transporte, gate de capital).
- Segredos nunca voltam pela API, não entram em log, journal ou backup
  comum; arranque resiliente (banco fora do ar **degrada**, não derruba).

## Começar em 2 minutos (local)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[desk,dev]"
ACCESS_PASSWORD=uma-senha-de-teste DATA_DIR=./data \
  uvicorn service.app:app --port 8080
```

Abra `http://localhost:8080`, use a senha definida e siga o quadro
**Seu progresso**. Com Docker:

```bash
ACCESS_PASSWORD=uma-senha-de-teste docker compose up --build
```

## Colocar no ar e vender
- **[INSTALAR.md](INSTALAR.md)** — Supabase, deploy, webhook, primeira venda.
- **[DOMINIO-E-CADASTROS.md](DOMINIO-E-CADASTROS.md)** — DNS, OAuth, Woovi.
- **[RUNBOOK.md](RUNBOOK.md)** — operação, incidentes, backup/restauração.
- **[COMECE-AQUI.md](COMECE-AQUI.md)** — guia do usuário final.

## Qualidade

```bash
pytest -q          # suíte completa
ruff check .
```

A suíte cobre os cenários de aceite da mesa (concorrência, disputa de
capital, mandato revogado, timeout incerto, fills fora de ordem, kill
switch, contabilidade que confere na mão) e o produto (isolamento entre
inquilinos, cobrança, cadastro, painel sem botão morto).

## Créditos e licença
Fork endurecido de
[TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)
(Apache-2.0). A Trading Floor é código original DZ23, inspirada visualmente
em [Claw3D](https://github.com/iamlukethedev/Claw3D) e
[openclaw-office](https://github.com/wickedapp/openclaw-office) (MIT) — ver
[NOTICE](NOTICE). Corretoras via [ccxt](https://github.com/ccxt/ccxt) (MIT).
Licença deste projeto: ver [LICENSE](LICENSE).
