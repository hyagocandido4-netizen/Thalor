# syntax=docker/dockerfile:1

FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/src \
    THALOR__PRODUCTION__PROFILE=docker \
    THALOR__SECURITY__DEPLOYMENT_PROFILE=docker

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash ca-certificates tini tzdata \
    && rm -rf /var/lib/apt/lists/*

ARG INSTALL_IQ=1

COPY requirements-ci.txt requirements.txt ./

RUN python -m pip install --upgrade pip \
    && pip install --no-cache-dir -r requirements-ci.txt \
    && if [ "$INSTALL_IQ" = "1" ]; then pip install --no-cache-dir -r requirements.txt; fi

COPY . .

RUN pip install --no-cache-dir -e . \
    && chmod +x /app/scripts/docker/*.sh \
    && useradd --create-home --home-dir /home/thalor --shell /bin/bash --uid 1000 thalor \
    && mkdir -p /app/runs /app/data /app/secrets /app/runs/backups /app/runs/logs /app/runs/control /app/runs/reports \
    && chown -R thalor:thalor /app /home/thalor

USER thalor

ENTRYPOINT ["/usr/bin/tini", "--", "/app/scripts/docker/entrypoint.sh"]
CMD ["bash", "/app/scripts/docker/runtime_status.sh"]
