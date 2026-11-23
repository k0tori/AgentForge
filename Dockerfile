FROM python:3.12-slim-bookworm

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src ./src
COPY toy-repo ./toy-repo
COPY --from=docker:27-cli /usr/local/bin/docker /usr/local/bin/docker

# Install CPU torch before the project so sentence-transformers does not pull CUDA wheels.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir .

ENV TOY_REPO_PATH=/app/toy-repo \
    TASK_DB_PATH=/var/lib/agentforge/tasks.sqlite \
    PYTHONUNBUFFERED=1

RUN mkdir -p /var/lib/agentforge

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
