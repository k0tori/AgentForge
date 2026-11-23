# 提交一个可复查的代码任务

这条路径不调用大模型。它证明任务从 API 到独立分支的闭环，不证明模型写代码的质量。

## 准备

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
export DETERMINISTIC_EXECUTOR=1
```

`DETERMINISTIC_EXECUTOR` 默认关闭。打开之后，下面这条请求只会把你给出的文件内容提交到副本仓库的新分支。它不执行这段代码，也不把 `SANDBOX_MODE` 改成 `host`。默认 `docker` 仍然只作用于真实 PGE 里的 pytest/ruff；守护进程不可用时那些命令失败，不会改在本机执行。

任务库是 `.agentforge/tasks.sqlite`，并且只由一个进程持有写锁。启动第二个 `uvicorn` 或 `--workers` 大于 1 时，后来的进程会拒绝成为执行者，不会把前一个进程还在跑的任务标成 interrupted。没有 Postgres 时这个演示任务仍能提交；健康检查里的 postgres 会是 false。

## 启动

```bash
uvicorn src.main:app --port 8000 --workers 1
```

另开一个终端，对 toy-repo 的副本提任务，避免把实验提交写进别的 git 仓库：

```bash
# 路径必须位于当前工作目录内。在仓库根目录把副本放进工作目录：
mkdir -p .agentforge/work
rm -rf .agentforge/work/toy-copy && cp -a toy-repo .agentforge/work/toy-copy
git -C .agentforge/work/toy-copy init -b main
git -C .agentforge/work/toy-copy -c user.email=you@example.com -c user.name=You add -A
git -C .agentforge/work/toy-copy -c user.email=you@example.com -c user.name=You commit -m "demo baseline"

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

请求字段是 `intent`，不是 `request`。`codebase.path` 必须是当前工作目录里 `.agentforge/` 下的真实目录，不能是符号链接，也不能指向仓库里的 `toy-repo` 本身。

同一个 `idempotency_key` 配上完全相同的请求会返回原来的任务。键相同但 intent、路径或文件内容不同，返回 409，不会去改第二个文件。

## 查询

```bash
curl -s http://127.0.0.1:8000/api/v1/tasks/<task_id>
```

演示做完时 `status` 为 `completed`，`result.demo_completed` 为 true，`result.acceptance` 为 `not_run`，`result.final_verdict` 为 null。这表示文件已经出现在分支上，传感器没有跑，**不是** PGE 的 PASS。`result.result_branch` 是分支名，`result.code_diff` 是 diff。

副本里的工作区文件不变。查看提交：

```bash
git -C .agentforge/work/toy-copy show <result_branch>:src/ping.py
```

发布要求副本已经是有提交的 Git 仓库。没有自己的 `.git`，或者还没有 HEAD，会在 `git init`、`add`、`commit`、`worktree` 之前拒绝，不会自动建仓库或 baseline。

结果分支从那个 HEAD 开始。任务相对种子快照改过的路径会整文件放上去：如果那个文件在种子里已经带有未提交的修改，分支上的文件是「脏内容 + 这次修改」，不是只含任务补丁。任务没碰到的脏文件仍只留在工作区，不会进分支，也不会被删掉。`.github` 这类没改到的已跟踪隐藏目录留在 HEAD 版本上。

`.env`、私钥、`credentials.json` 这类路径名不会被新加进分支，已跟踪的同类文件也不会被悄悄删掉。普通文件正文里的秘密不在这份名单里，发布不扫描文件内容。Git LFS、clean/smudge filter，以及把符号链接改成另一种链接，都不支持；配了 filter 会在检出前拒绝，因为 `core.hooksPath` 挡不住 filter。

还没创建提交或分支时，拒绝会说明没有 commit、也没有新分支。如果临时分支已经写出来之后才失败，错误会写出这条分支的名字，并说明它留着复查、不是验收结果。

持有写锁的进程退出后，再启动的进程仍能查到已完成结果。退出时还在 `pending` 或 `running` 的任务会变成 `interrupted`。如果 git 分支已经写出来、数据库还没记成 completed，状态也是 `interrupted`，`demo_completed` 不会变成 true，这条分支不能当成验收通过。另一个还活着的进程不会被这次启动收成 interrupted。

真实模型任务不要设置 `executor`（默认 `pge`）。它需要已有的 `DEEPSEEK_API_KEY` 和 `SANDBOX_MODE=docker` 的沙箱镜像。当前环境没有这份密钥，所以模型质量仍是未验证项。API 进程在 compose 里仍然挂着 Docker socket，沙箱容器本身不挂。
