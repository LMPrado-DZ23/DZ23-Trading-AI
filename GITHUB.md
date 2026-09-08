# Subir para o GitHub (2 minutos)

O repositório Git JÁ está pronto dentro desta pasta (commit + tag v0.6.0).
Falta só apontar para a tua conta:

1. Cria um repositório VAZIO e PRIVADO em github.com/new
   (nome sugerido: tradingagents-dz23). Não marque README/licença.
2. No PowerShell, dentro desta pasta:

   git remote add origin https://github.com/SEU_USUARIO/tradingagents-dz23.git
   git push -u origin main --tags

   (o Git vai pedir login — usa teu usuário + um Personal Access Token de
   github.com/settings/tokens com escopo "repo")

Pronto. Toda atualização futura: git add -A && git commit -m "..." && git push
