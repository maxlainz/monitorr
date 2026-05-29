# Build: instala dependencias y el proyecto (no-editable) en un venv aislado con uv.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

# Runtime: imagen mínima con solo el venv. Multi-arch (amd64/arm64): todas las deps tienen
# wheels precompilados, así que no se compila nada al construir arm64.
FROM python:3.12-slim
ENV PATH="/app/.venv/bin:$PATH" \
    MONITORR_CONFIG_DIR=/config
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
