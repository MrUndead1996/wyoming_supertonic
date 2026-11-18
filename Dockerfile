# syntax=docker/dockerfile:1
FROM debian:trixie-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_BREAK_SYSTEM_PACKAGES=1

RUN \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        git \
        build-essential \
        python3 \
        python3-dev \
        python3-pip \
        python3-venv \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /usr/src

# Install dependencies before copying source so code changes reuse this layer.
# Read requirements from pyproject.toml to avoid maintaining a duplicate list.
COPY pyproject.toml ./

# WITH_RU: include Russian auto-stress dependencies (extra time + image size).
ARG WITH_RU=false
RUN python3 - <<'PY'
import os
import tomllib
from pathlib import Path

project = tomllib.loads(Path("pyproject.toml").read_text())["project"]
requirements = list(project["dependencies"])
if os.environ["WITH_RU"] == "true":
    requirements.extend(project["optional-dependencies"]["ru"])
Path("/tmp/requirements.txt").write_text("\n".join(requirements) + "\n")
PY

# Cache packaging tools too; source-only rebuilds need no isolated build downloads.
RUN pip install --no-cache-dir "setuptools>=77" wheel -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

RUN apt-get autoremove --purge -y git build-essential

COPY ./ ./
RUN pip install --no-cache-dir --no-deps --no-build-isolation .

RUN chmod +x ./docker_run.sh && mkdir -p /data

# Default Wyoming port used by wyoming_supertonic
EXPOSE 10209

ENTRYPOINT ["./docker_run.sh"]
