# syntax=docker/dockerfile:1
#
# Thalor (NatBin) — container image
#
# Default build is "paper/CI mode" (no broker client).
# For runtime with IQ data collection, build with:
#   docker build --build-arg INSTALL_IQ=1 -t thalor:iq .
#
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Minimal system deps (TLS certs). If you build with INSTALL_IQ=1 and wheels are missing,
# you may need to extend this with build-essential / gcc.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ARG INSTALL_IQ=0

COPY requirements-ci.txt requirements.txt ./

RUN python -m pip install --upgrade pip \
    && pip install -r requirements-ci.txt \
    && if [ "$INSTALL_IQ" = "1" ]; then pip install -r requirements.txt; fi

COPY . .

RUN pip install -e .

# Default: prints the portfolio plan (no broker creds required).
CMD ["python","-m","natbin.runtime_app","portfolio","plan","--repo-root",".","--config","config/multi_asset.yaml","--json"]
