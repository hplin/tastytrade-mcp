# syntax=docker/dockerfile:1

FROM python:3.12-slim AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip wheel --wheel-dir /wheels .


FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="tastytrade-mcp" \
      org.opencontainers.image.description="Tastytrade MCP server with guarded trading tools" \
      org.opencontainers.image.source="https://github.com/hplin/tastytrade-mcp"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MCP_HTTP_HOST=0.0.0.0 \
    MCP_HTTP_PORT=8000 \
    ENABLE_LIVE_TRADING=false \
    FORCE_DRY_RUN=true

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app app

COPY --from=build /wheels /wheels
RUN python -m pip install --no-index --find-links=/wheels tastytrade-mcp \
    && rm -rf /wheels

USER 10001:10001

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"]

ENTRYPOINT ["tastytrade-mcp"]
CMD ["--transport", "http"]
