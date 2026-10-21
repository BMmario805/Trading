FROM node:24-bookworm-slim AS panel
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TRADING_DATA_DB=/var/lib/trading/data.db \
    TRADING_FRONTEND_DIR=/app/frontend/dist \
    TRADING_MT5_ENABLED=0 \
    TRADING_KEEP_AWAKE=0
WORKDIR /app/backend
COPY backend/requirements.txt backend/requirements-cloud.txt ./
RUN pip install --no-cache-dir -r requirements-cloud.txt \
    && useradd --uid 10001 --create-home trader \
    && mkdir -p /app/.runtime /var/lib/trading /backups \
    && chown -R trader:trader /app /var/lib/trading /backups
COPY --chown=trader:trader backend/app/ ./app/
COPY --chown=trader:trader deploy/ /app/deploy/
COPY --from=panel --chown=trader:trader /build/dist/ /app/frontend/dist/
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=8s --start-period=90s --retries=3 \
    CMD ["python", "/app/deploy/healthcheck.py"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--timeout-graceful-shutdown", "60"]
