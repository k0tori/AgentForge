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
- [x] 在途任务重启后标为 `interrupted`
- [x] 不调用模型的确定性任务：真实写文件并提交到分支
- [x] 失败、重复幂等键、并发任务不串状态，工作区不被覆盖

### P1

- [ ] 真实模型 smoke 只在已有凭据时跑，并单独报告，不与确定性闭环混为一谈
- [ ] 沙箱镜像随 API 一起发布，减少“忘了 build sandbox”的失败
- [ ] API 进程不再直接持有 Docker socket

## 本轮（M1）完成标准

- 文档里的确定性请求能在 toy-repo 副本上产生分支和 diff
- `GET /api/v1/tasks/{id}` 在重启后返回完成结果或 `interrupted`
- `pytest tests/integration/test_task_lifecycle.py` 通过
- 沙箱默认仍是 docker，不可用时不退回宿主执行

## 状态

M1 实现已进入当前分支，等待验收。
