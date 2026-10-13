# Marca DZ23 — original e derivados

- `dz23-logo-original.png` é a **fonte canônica** (1254×1254, RGBA,
  fundo transparente). Não editar nem regravar por cima.
- Todos os derivados em `service/static/marca/` são gerados por script a
  partir do original — não editar à mão:

```bash
uv run --no-project --with pillow python scripts/gerar_assets_marca.py
```

O script corta o símbolo (DZ23 + gráfico) na faixa vazia entre ele e o texto
"Trading AI", monta o ícone pequeno a partir do **símbolo** (texto a 16 px
vira mancha) e regenera favicon, `favicon.ico` e `dz23-icone.ico`
(multi-tamanho, para o executável e o instalador Windows).
