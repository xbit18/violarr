FROM node:24-bookworm-slim AS frontend-build

WORKDIR /build/frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build


FROM python:3.12-slim-bookworm

LABEL org.opencontainers.image.title="Violarr"
LABEL org.opencontainers.image.source="https://github.com/xbit18/violarr"
LABEL org.opencontainers.image.description="Violarr, a self-hosted Torznab indexer for ICVDB with PostgreSQL and automatic database updates"
LABEL org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LANG="en_US.utf8" \
    PATH="/opt/venv/bin:/usr/lib/postgresql/16/bin:$PATH" \
    PGDATA="/data/postgres" \
    DB_HOST="127.0.0.1" \
    DB_PORT="5432" \
    DB_NAME="icv_db" \
    DB_USER="icv" \
    DB_UPDATE_START_DELAY="60" \
    SNAPSHOT_STATE_FILE="/data/state/snapshot-version"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        gnupg \
    && install -d /usr/share/postgresql-common/pgdg /etc/postgresql-common \
    && curl --fail --show-error --silent \
        https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list \
    && echo "create_main_cluster = false" > /etc/postgresql-common/createcluster.conf \
    && printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d \
    && chmod +x /usr/sbin/policy-rc.d \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        gosu \
        locales \
        postgresql-16 \
        postgresql-client-16 \
        tini \
    && echo 'en_US.UTF-8 UTF-8' > /etc/locale.gen \
    && locale-gen en_US.UTF-8 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --home-dir /app --shell /usr/sbin/nologin app

WORKDIR /app

COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

COPY VERSION app.py snapshot_updater.py settings.py result_processor.py release_language.py prowlarr.py version.py webapi.py entrypoint.sh ./
COPY diagnostic_models.py search_diagnostics.py search_monitor.py ./
COPY --from=frontend-build /build/frontend/dist /app/frontend-dist

RUN chmod +x /app/entrypoint.sh

EXPOSE 8000

VOLUME ["/data"]

ENTRYPOINT ["/usr/bin/tini", "--", "/app/entrypoint.sh"]
