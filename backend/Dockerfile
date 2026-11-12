# syntax=docker/dockerfile:1
# --- Étape 1 : build du front ------------------------------------------------
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json* web/.npmrc ./
RUN npm ci
COPY web/ ./
RUN npm run build

# --- Étape 2 : image Python (API + worker) -----------------------------------
FROM python:3.12-slim AS app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
        libxml2 libxslt1.1 curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Version figée (celle qui a produit uv.lock) : une image reconstruite plus tard reste la même.
COPY --from=ghcr.io/astral-sh/uv:0.9.9 /uv /uvx /bin/

WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY backend/ ./
RUN uv sync --frozen --no-dev
COPY --from=web /web/dist /app/static

# Processus sans privilèges : une faille applicative ne donne pas root dans le conteneur.
RUN useradd --system --uid 10001 --no-create-home fluxus && chown -R fluxus:fluxus /app
USER fluxus

EXPOSE 8000
# FORWARDED_ALLOW_IPS : IP(s) du reverse proxy autorisées à fournir X-Forwarded-For ('*' = toutes, réseau Compose privé).
CMD ["sh", "-c", "alembic upgrade head && uvicorn fluxus.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips=\"${FORWARDED_ALLOW_IPS:-*}\""]
