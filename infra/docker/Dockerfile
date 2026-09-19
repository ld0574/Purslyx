# syntax=docker/dockerfile:1.7

ARG UV_VERSION=0.12.0
ARG PURSLYX_PYPI_INDEX_URL=https://pypi.org/simple/
FROM python:3.12-slim AS uv

ARG UV_VERSION
ARG PURSLYX_PYPI_INDEX_URL
RUN python -m pip install \
    --disable-pip-version-check \
    --no-cache-dir \
    --index-url "$PURSLYX_PYPI_INDEX_URL" \
    "uv==${UV_VERSION}"

FROM node:22-bookworm-slim AS web-builder

WORKDIR /build/web
COPY src/web/package.json src/web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY src/web/ ./
RUN npm run build

FROM python:3.12-slim AS runtime

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src" \
    PYTHONDONTWRITEBYTECODE="1" \
    PYTHONUNBUFFERED="1"

RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 purslyx \
    && useradd --system --uid 10001 --gid 10001 --create-home --home-dir /home/purslyx purslyx

WORKDIR /app

COPY --from=uv /usr/local/bin/uv /usr/local/bin/uvx /bin/
COPY pyproject.toml uv.lock ./
ARG PURSLYX_PYPI_INDEX_URL=https://pypi.org/simple/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv export \
      --frozen \
      --no-dev \
      --no-emit-project \
      --format requirements.txt \
      --no-header \
      --output-file /tmp/requirements.txt > /dev/null \
    && UV_DEFAULT_INDEX="$PURSLYX_PYPI_INDEX_URL" \
       UV_HTTP_TIMEOUT=30 \
       UV_HTTP_RETRIES=2 \
       uv venv /app/.venv \
    && UV_DEFAULT_INDEX="$PURSLYX_PYPI_INDEX_URL" \
       UV_HTTP_TIMEOUT=30 \
       UV_HTTP_RETRIES=2 \
       uv pip sync \
         --python /app/.venv/bin/python \
         /tmp/requirements.txt \
    && rm -f /tmp/requirements.txt
COPY src/ ./src
COPY migrations/ ./migrations
COPY scripts/ ./scripts
COPY alembic.ini ./
COPY --from=web-builder /build/web/dist ./src/web/dist

RUN mkdir -p /var/lib/purslyx \
    && chown -R purslyx:purslyx /app /var/lib/purslyx

USER purslyx
EXPOSE 8001

CMD ["/app/.venv/bin/uvicorn", "server.app.main:app", "--host", "0.0.0.0", "--port", "8001"]
