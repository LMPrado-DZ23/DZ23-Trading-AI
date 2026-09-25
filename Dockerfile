# TradingAgents DZ23 — imagem única: API + painel + mesa autônoma.
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
COPY . .
# [desk] = painel (fastapi/uvicorn), Postgres e corretoras/feed (ccxt).
RUN python -m pip install --no-cache-dir --upgrade pip wheel \
 && pip install --no-cache-dir ".[desk]"

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN useradd --create-home appuser \
 && install -d -m 0700 -o appuser -g appuser /data
WORKDIR /home/appuser/app
COPY --from=builder --chown=appuser:appuser /build .
USER appuser

VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).status==200 else 1)"

# Serviço com painel embutido. A CLI continua disponível:
#   docker run --entrypoint tradingagents <imagem> --help
CMD ["uvicorn", "service.app:app", "--host", "0.0.0.0", "--port", "8080"]
