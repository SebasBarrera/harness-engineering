# syntax=docker/dockerfile:1
# Governed Agent Harness container image.
# Multi-stage: the wheel is built with the PEP 517 front end and only the wheel is installed in
# the runtime stage. The base image is pinned by digest (python:3.12-slim, multi-arch index);
# Dependabot proposes digest updates.
ARG PYTHON_IMAGE=python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f

FROM ${PYTHON_IMAGE} AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /src
COPY pyproject.toml setup.py README.md LICENSE ./
COPY src ./src
RUN python -m pip install "build>=1.2,<2" \
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
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin harness
COPY --from=build /dist/ /tmp/dist/
RUN python -m pip install "$(find /tmp/dist -name 'governed_agent_harness-*.whl')[api]" \
    && rm -rf /tmp/dist
USER 10001:10001
WORKDIR /workspace
# No HEALTHCHECK: the default command is a one-shot CLI, not a long-running service.
ENTRYPOINT ["harness"]
CMD ["--help"]
