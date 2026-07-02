# Build: installs the dependencies and the project (non-editable) into an isolated venv with uv.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

# Runtime: minimal image with only the venv. Multi-arch (amd64/arm64): every dependency ships
# precompiled wheels, so nothing is compiled when building arm64.
FROM python:3.12-slim
# Build metadata (injected by the release workflow; empty on local builds).
ARG VERSION=0.0.0+dev
ARG VCS_REF=
ARG BUILD_DATE=
LABEL org.opencontainers.image.title="monitorr" \
      org.opencontainers.image.description="Watches viewing in Plex and manages episodes in Sonarr (monitor/keep/delete) API-only." \
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
# gosu lets the entrypoint drop from root to the PUID/PGID user so a bind-mounted /config is
# writable regardless of its host ownership.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r app && useradd -r -g app app \
    && mkdir -p /config && chown app:app /config
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh
# Start as root: the entrypoint remaps the app user, fixes /config ownership and drops privileges.
EXPOSE 8080
VOLUME /config
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/health').status==200 else 1)"]
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["monitorr"]
