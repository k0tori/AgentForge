FROM python:3.12-slim-bookworm

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src ./src
COPY toy-repo ./toy-repo

# Install CPU torch before the project so sentence-transformers does not pull CUDA wheels.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir .

ENV TOY_REPO_PATH=/app/toy-repo \
    TASK_DB_PATH=/var/lib/agentforge/tasks.sqlite \
    PYTHONUNBUFFERED=1

RUN groupadd --gid 10002 agentforge \
    && useradd --uid 10002 --gid 10002 --create-home --shell /usr/sbin/nologin agentforge \
    && mkdir -p /var/lib/agentforge /app/.agentforge/work \
    && chown agentforge:agentforge /var/lib/agentforge \
    && chown -R agentforge:agentforge /app/.agentforge

USER agentforge

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
