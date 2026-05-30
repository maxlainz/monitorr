# Build: instala dependencias y el proyecto (no-editable) en un venv aislado con uv.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

# Runtime: imagen mínima con solo el venv. Multi-arch (amd64/arm64): todas las deps tienen
# wheels precompilados, así que no se compila nada al construir arm64.
FROM python:3.12-slim
# Metadatos de build (los inyecta el workflow de release; vacíos en builds locales).
ARG VERSION=0.0.0+dev
ARG VCS_REF=
ARG BUILD_DATE=
LABEL org.opencontainers.image.title="monitorr" \
      org.opencontainers.image.description="Monitoriza el visionado en Plex y gestiona episodios en Sonarr (monitorizar/conservar/borrar) solo vía API." \
      org.opencontainers.image.source="https://github.com/maxlainz/monitorr" \
      org.opencontainers.image.url="https://github.com/maxlainz/monitorr" \
      org.opencontainers.image.documentation="https://github.com/maxlainz/monitorr#readme" \
      org.opencontainers.image.licenses="GPL-3.0-or-later" \
      org.opencontainers.image.authors="Max Lainz" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}"
ENV PATH="/app/.venv/bin:$PATH" \
    MONITORR_CONFIG_DIR=/config \
    MONITORR_BUILD_SHA="${VCS_REF}" \
    MONITORR_BUILD_DATE="${BUILD_DATE}"
WORKDIR /app
RUN groupadd -r app && useradd -r -g app app \
    && mkdir -p /config && chown app:app /config
COPY --from=builder --chown=app:app /app/.venv /app/.venv
USER app
EXPOSE 8080
VOLUME /config
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/health').status==200 else 1)"]
CMD ["monitorr"]
