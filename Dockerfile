FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /opt/harness
COPY pyproject.toml setup.py README.md LICENSE ./
COPY src ./src
COPY schemas ./schemas
COPY workflows ./workflows
COPY profiles ./profiles
RUN python -m pip install --no-cache-dir '.[api]'

WORKDIR /workspace
ENTRYPOINT ["harness"]
CMD ["--help"]
