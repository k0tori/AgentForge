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
- 把 Docker socket 从 API 进程里拿掉（已知风险，单独处理）

## Backlog

### P0

- [x] 任务记录落在 SQLite，重启可查
- [x] 前一个执行进程退出后，在途任务标为 `interrupted`；活着的执行进程不会被第二个进程回收
- [x] 确定性演示默认关闭：打开后只写 `.agentforge/` 下的副本，并提交到分支
- [x] 相同幂等键和相同请求复用任务；请求不同则 409。工作区不被覆盖

### P1

- [ ] 真实模型 smoke 只在已有凭据时跑，并单独报告，不与确定性闭环混为一谈
- [ ] 沙箱镜像随 API 一起发布，减少“忘了 build sandbox”的失败
- [ ] API 进程不再直接持有 Docker socket

## 本轮（M1）完成标准

- 文档里的确定性请求能在 `.agentforge/` 下的 toy-repo 副本上产生分支和 diff
- 演示结果的 `acceptance` 是 `not_run`，`final_verdict` 不是 PASS
- 持锁进程退出后，`GET` 返回完成结果或 `interrupted`
- 第二个进程不能把活着的任务收成 interrupted，并且 GET 读到的是 SQLite 里的当前状态
- `pytest tests/integration/test_task_lifecycle.py tests/integration/test_task_processes.py` 在 CI 的 test job 里跑
- `SANDBOX_MODE=docker pytest tests/sandbox -m sandbox` 在 CI 的 sandbox job 里跑
- 沙箱默认仍是 docker，不可用时不退回宿主执行

## 状态

M1 仍在 PR 2 上加固，还没有当作验收通过。

## 已知风险

- 任务库同一时间只有一个写进程。这是故意的限制，不是多 worker 执行。
- 确定性路径是演示。`completed` 只表示分支写完，不表示 PGE 验收通过。
- 发布用跨进程文件锁。锁在临时目录，按仓库路径分开。
- 进程在 `git commit` 之后、结果写成 completed 之前退出时，任务是 `interrupted`。分支名会出现在错误说明里，不能当成成功。
- `docker-compose.yml` 的 API 服务仍然挂载 Docker socket。沙箱容器不挂 socket、不带宿主环境、无网络。
- 没有配置 `DEEPSEEK_API_KEY`。真实模型 smoke 未跑，也不向用户要密钥。
