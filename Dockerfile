# syntax=docker/dockerfile:1.7
FROM --platform=linux/amd64 node:22-bookworm-slim@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436 AS headless
ARG OBSIDIAN_HEADLESS_VERSION=0.0.14
RUN npm install --global --prefix /opt/obsidian "obsidian-headless@${OBSIDIAN_HEADLESS_VERSION}" \
    && /opt/obsidian/bin/ob --version

FROM --platform=linux/amd64 python:3.13-slim-bookworm@sha256:00faa2debb87529f9f0764e9491d8ba400a3678976616c3bd7cb193745ac20d1 AS python-build
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /build
RUN pip install --no-cache-dir uv==0.8.24
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM --platform=linux/amd64 python:3.13-slim-bookworm@sha256:00faa2debb87529f9f0764e9491d8ba400a3678976616c3bd7cb193745ac20d1 AS runtime
ARG APP_VERSION=0.1.0
LABEL org.opencontainers.image.title="tasknotes-ntfy" \
      org.opencontainers.image.version="${APP_VERSION}"
RUN apt-get update \
    && apt-get install --yes --no-install-recommends libstdc++6 tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 vaultread \
    && groupadd --gid 10002 notifier \
    && useradd --uid 10001 --gid vaultread --no-create-home --shell /usr/sbin/nologin obsync \
    && useradd --uid 10002 --gid notifier --groups vaultread --no-create-home \
        --shell /usr/sbin/nologin notifier \
    && mkdir -p /data /opt/app \
    && chmod 0750 /data
COPY --from=headless /usr/local/bin/node /usr/local/bin/node
COPY --from=headless /opt/obsidian /opt/obsidian
COPY --from=python-build /opt/venv /opt/venv
COPY docker/entrypoint.sh docker/healthcheck.sh /usr/local/bin/
ENV PATH="/opt/venv/bin:/opt/obsidian/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    XDG_CONFIG_HOME=/data/config
VOLUME ["/data"]
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=10s --start-period=2m --retries=3 \
    CMD ["/usr/local/bin/healthcheck.sh"]
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
