# INSTALAR — DZ23 Trading AI

Guia de instalação do produto real: **um dono por instalação**, sem SaaS,
sem cobrança. Três caminhos — Windows (principal), a partir do código
(desenvolvimento) e servidor (opcional, acesso remoto).

## 1. Windows (recomendado — sem Python no computador)

O instalador é gerado pelo pipeline deste repositório (workflow
**Windows Release Build** → artefato `dz23-windows-x64`, com
`DZ23-TradingAI-Setup-<versão>.exe` e `SHA256SUMS`). A publicação de Release
oficial aguarda aprovação do proprietário; até lá, use o artefato do CI e
**confira o SHA-256** antes de executar.

1. Execute o Setup (duplo clique). Ele não exige Python no computador.
2. Na primeira abertura, o **assistente do dono** cria a sua senha e mostra
   o **código de recuperação** — guarde-o: é exibido uma única vez.
3. O painel abre no navegador em `http://localhost:8000` (porta ocupada →
   o launcher usa a próxima livre, faixa 8000–8010). Para encerrar o
   aplicativo use o botão **Fechar aplicativo** (encerramento gracioso).
4. Seus dados ficam em `%APPDATA%\DZ23-Trading-AI` (configuração, estado da
   mesa, logs). Nada vive no diretório de instalação.

**Atualizar:** rode o Setup da versão nova — ele detecta a instalação e
oferece Atualizar/Reparar. Antes de trocar de versão é criado um **backup
verificado** em `%APPDATA%\DZ23-Trading-AI-backups`; se o backup falhar, a
instalação é abortada para proteger seus dados. Downgrade só com
`/FORCEDOWNGRADE=1` (também com backup verificado antes).

## 2. A partir do código (desenvolvimento)

```bash
uv sync --locked --extra desk --extra dev
uv run uvicorn service.app:app --port 8080
```

Abra `http://localhost:8080/app`: o mesmo assistente de primeira execução
aparece no primeiro acesso.

## 3. Servidor (opcional — acesso remoto)

Para navegar do celular ou de outra máquina, rode em Docker
(`Dockerfile`/`docker-compose.yml` deste repositório) numa VM Linux — passo
a passo em **DEPLOY.md**. A mudança PC → Oracle Cloud ARM64 tem roteiro no
**RUNBOOK.md**; a homologação no Oracle ainda **não** foi realizada (nada é
declarado homologado sem evidência registrada).

## 4. Recuperação e operação

- **RUNBOOK.md** — incidentes, backup/restauração, troca de servidor,
  senha bloqueada, chave vazada.
- **COMECE-AQUI.md** — guia do usuário final (primeiros passos, mesa de
  agentes, copiloto manual).
- Esqueceu a senha? Use o **código de recuperação** do assistente de
  primeira execução; sem ele, restaure pelo backup.

## Limites honestos

- A mesa opera em **PAPER** (simulado) e **TESTNET** (dinheiro fictício da
  exchange). **LIVE (dinheiro real) está bloqueado por construção**
  (fail-closed) e não é ativável pela interface.
- Software experimental de pesquisa e engenharia: nada aqui é recomendação
  de investimento, não garante lucro e pode causar perdas. Resultado de
  teste não é promessa de resultado real.
