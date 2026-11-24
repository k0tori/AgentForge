# 执行沙箱

生成出来的代码、pytest 和 ruff 默认在一次性容器里运行，不在 AgentForge 进程所在的宿主上执行。

## 构建镜像

```bash
docker compose up -d --build
```

这会构建沙箱镜像，并先跑完一个立刻退出的 `sandbox` 服务，让镜像留在本地。该服务不挂 Docker socket，也不常驻。`docker compose build sandbox` 只构建镜像。只启动数据库时用 `docker compose up -d postgres redis`，那不会构建沙箱镜像。

镜像不在本地时执行端失败，`--pull never`，不会 `docker pull`，也不会改在宿主上跑。执行端启动时把 `agentforge-sandbox:latest` 解析成当时的本地 `sha256` 镜像 id，之后的 `docker run` 只用这个 id。之后即使有人把 `:latest` 指到别的镜像，已经钉住的进程仍跑原来的 id；这个 id 被删掉则失败，而不是跟着新标签走。执行端要在镜像准备好之后启动。默认 Compose 里 `executor` 等这个一次性服务成功结束。

## 模式

| `SANDBOX_MODE` | 行为 |
|---|---|
| `docker`（默认） | API 把 pytest/ruff 交给本机执行端，由执行端启动容器。执行端不可达、镜像缺失或 API 自己能连上 Docker socket 时失败，**不会**改在宿主上跑。 |
| `host` | 在当前进程里跑 pytest/ruff。只应在你信任工作区内容时手动打开。它能读到本机文件和环境变量。 |

每次 pytest/ruff 由执行端单独启动容器，连接结束、超时或执行端重启时删除。容器只挂载该次工作区到 `/workspace`，根文件系统只读，`/tmp` 是内存盘。

## 容器里没有的东西

- 不挂载 Docker socket，容器里也没有 Docker 客户端
- 不继承宿主环境变量（没有 API key）
- 网络是 `none`
- 丢掉全部 Linux capabilities，并设置 `no-new-privileges`
- 内存、CPU、进程数有上限；命令超时或 PGE 截止时间到达后，这次启动的容器会被 `docker rm -f` 删除

PGE 截止时间会取消正在等待的协程，并在下一次检查时停止新的 git 命令和发布。已经进入 `subprocess` 的那一条 git 命令不会被杀掉；它返回之后，结果不会被记成 PASS，后续的 `set_result` 也不能把 `failed` 改回 `completed`。超时不等于把所有线程都停掉。

文件读写仍由宿主上的工具完成，但只写 sprint 工作区。真正执行测试和 lint 的是容器里的 Python。

## Compose

`executor` 是唯一挂载宿主 Docker socket 的服务，并且以 root 运行。`api` 不挂这个 socket，镜像里也没有 docker CLI，进程用户是 uid/gid `10002`，不在 docker 组。两边把宿主目录 `/var/lib/agentforge/workspaces` 挂到同一个绝对路径；执行端拿这个宿主路径去做 `docker run -v`，不会把执行端容器自己的 `/tmp` 交给宿主 Docker。

API 只通过 `/run/agentforge/executor.sock` 请求 `pytest` 或 `ruff`。请求里不能带镜像、挂载、环境变量、Docker 参数或 shell。执行端核对连接进程的 uid，不是 `10002` 就拒绝。工作区根目录由执行端收成 root 所有且 API 用户不可写，运行目录只能是它创建的一层子目录；根目录本身、`..`、符号链接都会拒绝。这不是做一次 `realpath` 就结束：API 用户写不了父目录，所以换不成符号链接。父目录若因为部署变成 API 可写，执行端拒绝启动。POSIX ACL 不在这次检查里，能写父目录的 root 进程仍可能在 dockerd 打开路径前换掉目录。

每个执行端有一个稳定的安装标识 `SANDBOX_EXECUTOR_ID`。它写在执行端自己的环境里，请求协议没有这个字段，客户端不能指定，也不能拿它去清理别的安装。Compose 把它设成当前项目名（`COMPOSE_PROJECT_NAME`），同一次安装重启后仍是这个值，不会每次启动随机生成。没设置、空白或含空格等非法字符时，执行端直接退出，并且不删除任何容器。两个进程配了同一个标识，就属于同一个清理域：任何一个重启都会删掉这个标识下的沙箱容器，包括另一个进程还在跑的。不同安装要隔离开，必须使用不同的标识。

执行端重启只删除同时带有 `agentforge.managed=1` 和自己的 `agentforge.executor=<标识>` 的容器。进行中的命令因此失败，不会改在宿主上跑。客户端断开或超时只删除这一次启动的那个容器。`SANDBOX_MODE=host` 仍是显式的本机开发选择，不是默认，也不是执行端失败后的退路。API 进程如果自己能连上 Docker socket，预检直接失败。

配置里的名字仍是 `agentforge-sandbox:latest`。请求不能改这个名字。真正执行时用的是启动时钉住的本地摘要。

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
