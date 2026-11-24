# AgentForge 路线

## 目标用户

要在自己的仓库里把一个功能需求变成可审查 diff 的开发者。他们用 CLI 或 HTTP API，不先要聊天界面。

## 核心用例

1. 提交一个代码任务，指定本地仓库路径。
2. 查询任务是在跑、失败、被中断，还是完成。
3. 完成后在独立 git 分支上看 diff，当前工作区不被覆盖。
4. 进程重启后仍能查到已完成的结果；当时还在跑的任务被标成中断，而不是一直 running。

## 非目标

- 多角色编排、RAGAS、回放、校准套件
- Web UI
- 用真实大模型分数证明生成质量

## Backlog

### P0

- [x] 任务记录落在 SQLite，重启可查
- [x] 前一个执行进程退出后，在途任务标为 `interrupted`；活着的执行进程不会被第二个进程回收
- [x] 确定性演示默认关闭：打开后只写 `.agentforge/` 下的副本，并提交到分支
- [x] 相同幂等键和相同请求复用任务；请求不同则 409。工作区不被覆盖

### P1

- [ ] 真实模型 smoke 只在已有凭据时跑，并单独报告，不与确定性闭环混为一谈
- [x] API 进程不再直接持有 Docker socket（M3，`03e02cbc65f52ade5acd85c0d06a54d9be6cca5e`）
- [ ] 沙箱镜像随默认 Compose 构建发布，并在进程内固定本地 image ID（M4，本分支，待验收）

## 本轮（M1）完成标准

- 文档里的确定性请求能在 `.agentforge/` 下的 toy-repo 副本上产生分支和 diff
- 演示结果的 `acceptance` 是 `not_run`，`final_verdict` 不是 PASS
- 持锁进程退出后，`GET` 返回完成结果或 `interrupted`
- 第二个进程不能把活着的任务收成 interrupted，并且 GET 读到的是 SQLite 里的当前状态
- `pytest tests/integration/test_task_lifecycle.py tests/integration/test_task_processes.py` 在 CI 的 test job 里跑
- `SANDBOX_MODE=docker pytest tests/sandbox -m sandbox` 在 CI 的 sandbox job 里跑
- 沙箱默认仍是 docker，不可用时不退回宿主执行

## M1 已验收

验收范围只有三件事：单写进程、SQLite 持久任务记录、确定性演示闭环。

这不包括真实 PGE 模型质量，也不包括多 worker 执行。

- head：`1c82cdd8b8b484e826019a85e7eede77d46fff83`
- CI：push [45155636618](https://github.com/k0tori/AgentForge/actions/runs/45155636618) 与 pull_request [45155642750](https://github.com/k0tori/AgentForge/actions/runs/45155642750)，test 与 sandbox 都成功
- 双进程证据：`tests/integration/test_task_processes.py`（活进程不被回收、退出后才 interrupted、publish 跨进程锁）

## M2

默认 PGE 路径遇到缺密钥、Docker 不可用、镜像不在本地、模型超时或失败时，任务在有限时间内变成 `failed`，`final_verdict` 不是 PASS，错误可在重启后查询。检索依赖不可用时任务可以继续，结果里带 `retrieval_unavailable`。

故障注入在 `tests/unit/test_failure_reporting.py` 和 `tests/integration/test_pge_failures.py`。它们不调用付费模型，也不代替 `tests/sandbox` 的真实容器证据。

超时的边界：截止时间会取消正在 `await` 的协程，并让本次沙箱子进程在轮询中被杀掉、容器被删除。已经进入 `subprocess` 的 git 命令会跑完这一条，但不会再开始下一条，也不会把任务收成 PASS。它不会把任意后台线程停掉。

M1 与 M2 已按限定范围在 PR #2 验收并冻结，head 是 `0f4cad76861032b3d6666eff94256340271f440a`。这不包括真实模型质量，也不包括多 worker。

## M3 已验收

PR #3，head `03e02cbc65f52ade5acd85c0d06a54d9be6cca5e`。API 不持有 Docker socket；执行端通过 Unix socket 只跑 pytest/ruff。重启只回收本安装标识下的容器。

## M4

默认 `docker compose up -d --build` 构建沙箱镜像，不再依赖单独的 sandbox profile。执行端在进程内固定 `docker image inspect` 的本地 image ID，`docker run` 使用这个 ID，`--pull never`。这不是 registry digest，重启会重新解析当前标签。镜像不在本地或已固定的 ID 被删除时失败，不拉取，不在宿主执行。

未做：真实模型 smoke、git 发布进沙箱、多 worker、UI、POSIX ACL、容器逃逸证明、向外部仓库推镜像。

## 已知风险

- 任务库同一时间只有一个写进程。这是故意的限制，不是多 worker 执行。
- 确定性路径是演示。`completed` 只表示分支写完，不表示 PGE 验收通过。
- 发布用跨进程文件锁。锁在临时目录，按仓库路径分开。
- 进程在 `git commit` 之后、结果写成 completed 之前退出时，任务是 `interrupted`。分支名会出现在错误说明里，不能当成成功。
- `executor` 持有 Docker socket。`api` 不挂 socket、不以 root 运行、镜像里没有 docker CLI。沙箱容器不挂 socket、不带宿主环境、无网络。执行端被攻破仍然等于宿主 Docker 权限；容器逃逸不在这条边界里被证明不可能。
- 没有配置 `DEEPSEEK_API_KEY`。真实模型 smoke 未跑。缺密钥时任务失败，不向用户要密钥，也不发起模型请求。
