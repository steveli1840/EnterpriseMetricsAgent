FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml /app/
COPY backend /app/backend
COPY metrics /app/metrics
COPY knowledge /app/knowledge
COPY config /app/config
COPY data/clickhouse /app/data/clickhouse
RUN pip install --upgrade pip && pip install .
ENV PYTHONPATH=/app/backend
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
