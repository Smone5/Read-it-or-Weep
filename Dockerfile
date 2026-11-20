# One image for the whole product: the API, the host voice channel, the live-game stream
# and the web app. Runs anywhere containers run (Azure Container Apps, AWS ECS / App Runner,
# Google Cloud Run, Kubernetes, a single VM). It needs only a PostgreSQL database.
#
#   docker build -t read-it-or-weep .
#   docker run -p 8100:8100 --env-file backend/.env -e DATABASE_URL=postgresql://... read-it-or-weep

# ---- build the web app ----------------------------------------------------------
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ---- the runtime -----------------------------------------------------------------
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 \
    MPLCONFIGDIR=/tmp/matplotlib \
    WEB_DIST_DIR=/app/web \
    STORAGE_BACKEND=local STORAGE_LOCAL_DIR=/data/storage \
    PORT=8100
WORKDIR /app/backend

COPY backend/requirements.txt backend/requirements-cloud.txt ./
RUN pip install -r requirements.txt -r requirements-cloud.txt

COPY backend/ ./
COPY --from=web /web/dist /app/web

# Run as an unprivileged user; /data holds uploaded reports when STORAGE_BACKEND=local.
RUN useradd --system --uid 10001 app && mkdir -p /data/storage && chown -R app /data
USER app
VOLUME ["/data"]
EXPOSE 8100
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('PORT','8100'), timeout=4)"

# One process: live-game state lives in the database, so you scale by running more containers.
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8100} --proxy-headers --forwarded-allow-ips='*'"]
