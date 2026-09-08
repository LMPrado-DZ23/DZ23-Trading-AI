FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
COPY requirements.txt* pyproject.toml* setup.py* ./
COPY tradingagents/ tradingagents/
COPY cli/ cli/
COPY service/ service/
RUN pip install --no-cache-dir -e . && pip install --no-cache-dir fastapi uvicorn
VOLUME /data
EXPOSE 8080
ENV DATA_DIR=/data
CMD ["uvicorn","service.app:app","--host","0.0.0.0","--port","8080"]
