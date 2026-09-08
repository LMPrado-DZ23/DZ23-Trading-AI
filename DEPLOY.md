# Deploy legado — não usar para capital real

Este documento descrevia o serviço monousuário da v1.1.0. A rota direta de
ordens foi retirada por segurança e o deploy abaixo não representa a plataforma
SaaS A5 nem autorização para operar dinheiro.

O procedimento de produção será substituído no marco M9 e só será considerado
válido após instalação limpa, migrations, backup/restore, TLS e smoke no domínio.

## Referência histórica da v1.1.0

Serviço legado num único processo: API, configuração e painel básico.

## 1. Criar o recurso
Coolify → + New Resource → Dockerfile → aponte para esta pasta
(ou suba a um repo Git privado e aponte para ele).

## 2. Definir UMA variável e UM volume
- Environment Variables:  ACCESS_PASSWORD = (uma senha forte tua)
- Storages/Volumes:       /data   (persistente — guarda config e histórico)

## 3. Domínio e Deploy
Ex.: tradingagents.dz23.online → Deploy.

## Usar (para sempre)
Abrir o domínio → digitar a senha → aba ⚙️ Configurações: preencher as
chaves (IA, FRED, Alpha Vantage, exchange) → 💾 Salvar (vale no próximo job,
SEM reiniciar nada) → aba 📈 Operação: NVDA + data → ▶ Analisar agora →
acompanhar o card até CONCLUÍDA e ler a decisão.

## Segurança
- Uma senha (Bearer) protege painel e API; /healthz é o único público.
- Chaves ficam em /data/config.json (0600) no SERVIDOR; na tela, sempre
  mascaradas (•••últimos4). /docs desligado.
- A configuração/ordem de corretora legada está desabilitada. O novo fluxo
  exige tenant, mandato, risco, intenção, aprovação, reserva e reconciliação.
- Acesso protegido por trava anti-força-bruta (5 erros => 15 min).
