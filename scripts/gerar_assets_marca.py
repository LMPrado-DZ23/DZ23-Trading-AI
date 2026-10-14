"""Gera os assets da marca DZ23 a partir do original — sem perder o original.

Fonte canônica e intocada: `assets/brand/dz23-logo-original.png`.
Derivados gravados em `service/static/marca/`:

- `dz23-logo.png`, `dz23-logo-512.png`, `dz23-logo-192.png` — logomarca
  completa (símbolo + "Trading AI"), em tela quadrada transparente.
- `dz23-simbolo-512.png`, `dz23-simbolo-192.png`, `dz23-simbolo-64.png` —
  apenas o símbolo (DZ23 + gráfico), para cabeçalhos e telas pequenas.
- `favicon-32.png`, `favicon-64.png`, `favicon.ico` — o SÍMBOLO reduzido,
  não a logomarca inteira: texto a 16 px vira mancha ilegível.
- `dz23-icone.ico` — ícone do executável/instalador Windows (multi-tamanho,
  inclusive 256 px).

Reprodução (o Pillow não é dependência do projeto; roda isolado):

    uv run --no-project --with pillow python scripts/gerar_assets_marca.py
"""

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / "assets" / "brand" / "dz23-logo-original.png"
DESTINO = ROOT / "service" / "static" / "marca"

# No original (1254x1254) as linhas 905-915 ficam vazias entre o símbolo e o
# texto "Trading AI"; o corte fixa o meio da faixa.
CORTE_TEXTO = 910
LIMITE_ALFA = 16


def _conteudo(im: Image.Image) -> Image.Image:
    """Recorta a imagem para o retângulo com conteúdo visível."""
    mascara = im.getchannel("A").point(lambda a: 255 if a > LIMITE_ALFA else 0)
    caixa = mascara.getbbox()
    return im.crop(caixa) if caixa else im


def _quadrado(im: Image.Image, lado: int, margem: float = 0.05) -> Image.Image:
    """Centraliza o conteúdo numa tela quadrada transparente."""
    tela = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
    area = int(lado * (1 - 2 * margem))
    escala = min(area / im.width, area / im.height)
    novo = im.resize(
        (max(1, round(im.width * escala)), max(1, round(im.height * escala))),
        Image.LANCZOS,
    )
    tela.paste(novo, ((lado - novo.width) // 2, (lado - novo.height) // 2), novo)
    return tela


def main() -> None:
    original = Image.open(ORIGINAL).convert("RGBA")
    completo = _conteudo(original)
    simbolo = _conteudo(original.crop((0, 0, original.width, CORTE_TEXTO)))

    DESTINO.mkdir(parents=True, exist_ok=True)

    for lado in (1024, 512, 192):
        nome = "dz23-logo.png" if lado == 1024 else f"dz23-logo-{lado}.png"
        _quadrado(completo, lado).save(DESTINO / nome, "PNG", optimize=True)

    for lado in (512, 192, 64):
        _quadrado(simbolo, lado).save(
            DESTINO / f"dz23-simbolo-{lado}.png", "PNG", optimize=True
        )

    _quadrado(simbolo, 32, margem=0.03).save(
        DESTINO / "favicon-32.png", "PNG", optimize=True
    )
    _quadrado(simbolo, 64, margem=0.03).save(
        DESTINO / "favicon-64.png", "PNG", optimize=True
    )
    icone = _quadrado(simbolo, 256, margem=0.03)
    icone.save(
        DESTINO / "favicon.ico",
        "ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    icone.save(
        DESTINO / "dz23-icone.ico",
        "ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )

    for arquivo in sorted(DESTINO.glob("*")):
        print(f"{arquivo.name}: {arquivo.stat().st_size} bytes")


if __name__ == "__main__":
    main()
