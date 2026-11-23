# 执行沙箱

生成出来的代码、pytest 和 ruff 默认在一次性容器里运行，不在 AgentForge 进程所在的宿主上执行。

## 构建镜像

```bash
docker build -f Dockerfile.sandbox -t agentforge-sandbox:latest .
```

`docker compose --profile sandbox build sandbox` 做同一件事。

`docker compose up` 不会构建这个镜像。`sandbox` 在 profile `sandbox` 里，也不是 `api` 的 `depends_on`。只执行 `up` 时，默认 PGE 会在跑测试前因为本地没有 `agentforge-sandbox:latest` 而失败，并且不会改到宿主上执行，也不会自动 `docker pull`。

## 模式

| `SANDBOX_MODE` | 行为 |
|---|---|
| `docker`（默认） | 每次命令启动一个容器。Docker 不可用、镜像缺失或启动失败时抛出错误，**不会**改在宿主上跑。 |
| `host` | 在当前进程里跑 pytest/ruff。只应在你信任工作区内容时手动打开。它能读到本机文件和环境变量。 |

生成和评估各自有一次尝试。同一次尝试里的容器带同一个 `agentforge.attempt` 标签，尝试结束时删除。容器只挂载该次 sprint 工作区到 `/workspace`，根文件系统只读，`/tmp` 是内存盘。

## 容器里没有的东西

- 不挂载 Docker socket，容器里也没有 Docker 客户端
- 不继承宿主环境变量（没有 API key）
- 网络是 `none`
- 丢掉全部 Linux capabilities，并设置 `no-new-privileges`
- 内存、CPU、进程数有上限；命令超时或 PGE 截止时间到达后，这次启动的容器会被 `docker rm -f` 删除

PGE 截止时间会取消正在等待的协程，并在下一次检查时停止新的 git 命令和发布。已经进入 `subprocess` 的那一条 git 命令不会被杀掉；它返回之后，结果不会被记成 PASS，后续的 `set_result` 也不能把 `failed` 改回 `completed`。超时不等于把所有线程都停掉。

文件读写仍由宿主上的工具完成，但只写 sprint 工作区。真正执行测试和 lint 的是容器里的 Python。

## Compose

`api` 服务挂了宿主的 Docker socket，这样 API 进程才能启动沙箱。**socket 不会进入沙箱容器。** 持有 socket 的 API 进程本身等同于能操作宿主 Docker，这是编排器的残留风险，不要把同一挂载复制到沙箱。

## 测试

策略测试不启动容器：

```bash
pytest tests/unit/test_sandbox_policy.py
```

真实容器测试会构建镜像并运行容器。没有 Docker 时这一组失败，而不是跳过：

```bash
SANDBOX_MODE=docker pytest -m sandbox
```

日常的 `pytest -m "not integration"` 显式使用 `SANDBOX_MODE=host`，只覆盖不需要守护进程的逻辑。
