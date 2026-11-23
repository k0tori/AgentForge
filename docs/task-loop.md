# 提交一个可复查的代码任务

这条路径不调用大模型。它证明任务从 API 到独立分支的闭环，不证明模型写代码的质量。

## 准备

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
export SANDBOX_MODE=host
```

`SANDBOX_MODE=host` 会在本机跑 pytest/ruff。只在你信任仓库内容时使用。默认 `docker` 在没有守护进程时会失败，而不会偷偷改在本机执行。

任务记录写在 `.agentforge/tasks.sqlite`。没有 Postgres 时 API 仍能接这个确定性任务；健康检查里的 postgres 会是 false。

## 启动

```bash
uvicorn src.main:app --port 8000
```

另开一个终端，对 toy-repo 的副本提任务，避免把实验提交写进别的 git 仓库：

```bash
# 路径必须位于当前工作目录内。在仓库根目录把副本放进工作目录：
mkdir -p .agentforge/work
rm -rf .agentforge/work/toy-copy && cp -a toy-repo .agentforge/work/toy-copy

curl -s -X POST http://127.0.0.1:8000/api/v1/tasks/ \
  -H 'Content-Type: application/json' \
  -d '{
    "intent": "Add a ping helper without a model",
    "codebase": {"type": "local_path", "path": ".agentforge/work/toy-copy"},
    "options": {
      "executor": "deterministic",
      "idempotency_key": "ping-once",
      "change": {
        "path": "src/ping.py",
        "content": "def ping() -> str:\n    return \"pong\"\n"
      }
    }
  }'
```

请求字段是 `intent`，不是 `request`。重复同一个 `idempotency_key` 会返回原来的任务，不会再改一次。

## 查询

```bash
curl -s http://127.0.0.1:8000/api/v1/tasks/<task_id>
```

完成后 `status` 为 `completed`，`result.result_branch` 是分支名，`result.code_diff` 是 diff。`result.note` 写明这次没有调用模型。

副本里的工作区文件不变。查看提交：

```bash
git -C .agentforge/work/toy-copy show <result_branch>:src/ping.py
```

服务重启后，同一条 `GET` 仍能查到完成结果。重启时还在 `pending` 或 `running` 的任务会变成 `interrupted`，并带有错误说明，不会继续显示 running。

真实模型任务省略 `options.executor`（默认 `pge`），并需要可用的 `DEEPSEEK_API_KEY` 与 `SANDBOX_MODE=docker` 的沙箱镜像。那不是这条确定性验收。
