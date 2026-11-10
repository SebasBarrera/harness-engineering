# syntax=docker/dockerfile:1
# Governed Agent Harness container image.
# Multi-stage: the wheel is built with the PEP 517 front end and only the wheel is installed in
# the runtime stage. The base image is pinned by digest (python:3.12-slim, multi-arch index);
# Dependabot proposes digest updates.
ARG PYTHON_IMAGE=python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

FROM ${PYTHON_IMAGE} AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /src
COPY pyproject.toml setup.py README.md LICENSE ./
COPY src ./src
COPY requirements/tools-build.txt /tmp/requirements/
# Every pip install is hash-pinned (requirements/, scripts/lock_requirements.py).
RUN python -m pip install --require-hashes -r /tmp/requirements/tools-build.txt \
    && python -m build --wheel --outdir /dist

FROM ${PYTHON_IMAGE} AS runtime
LABEL org.opencontainers.image.title="Governed Agent Harness" \
      org.opencontainers.image.description="Local control plane that governs AI-assisted software development" \
      org.opencontainers.image.source="https://github.com/SebasBarrera/harness-engineering" \
      org.opencontainers.image.licenses="Apache-2.0"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
# Git is required: the harness captures baselines and ChangeSets from the repository it governs.
# apt-get upgrade installs the security updates of the base image's own packages (zlib, apt,
# liblzma, ca-certificates, ...) that the pinned base does not carry yet.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin harness
COPY --from=build /dist/ /tmp/dist/
COPY requirements/tools-pip.txt requirements/runtime-api.txt /tmp/requirements/
# A current pip first (the base image's is older), then the locked runtime set and the wheel,
# so nothing is resolved at build time.
RUN python -m pip install --require-hashes -r /tmp/requirements/tools-pip.txt \
    && python -m pip install --require-hashes -r /tmp/requirements/runtime-api.txt \
    && python -m pip install --no-deps "$(find /tmp/dist -name 'governed_agent_harness-*.whl')[api]" \
    && rm -rf /tmp/dist /tmp/requirements
USER 10001:10001
WORKDIR /workspace
# No HEALTHCHECK: the default command is a one-shot CLI, not a long-running service.
ENTRYPOINT ["harness"]
CMD ["--help"]
