# Toy-Repo 编码规范

> 本文件是 AgentForge 的 Guide 层核心文件。
> Planner 读取它来理解项目模式，Evaluator 的风格评审 Sensor 对照它判断代码合规性。

---

## 1. 命名规范

| 元素 | 规范 | 示例 |
|------|------|------|
| Python 变量/函数/模块 | snake_case | `get_user_by_id` |
| Python 类 | PascalCase | `UserCreate` |
| 数据库表名 | snake_case, 复数 | `users`, `notes` |
| 数据库列名 | snake_case | `created_at`, `owner_id` |
| JSON 字段 | camelCase（由 Pydantic alias 配置） | `createdAt` |
| 路由路径 | kebab-case | `/api/v1/users/{user_id}` |

## 2. 分层架构

每个资源严格遵循四层结构：

```
models/   → SQLModel 数据库模型（table=True）
schemas/  → Pydantic 请求/响应模型（不绑定数据库）
services/ → 业务逻辑层（纯 async 函数，接收 AsyncSession）
routers/  → FastAPI 路由层（只做 HTTP 映射，调用 service）
```

**规则**：
- Router 不直接操作数据库，只调用 Service
- Service 不返回 Response 对象，返回 ORM 模型或 None
- Schema 不继承 SQLModel，保持纯 Pydantic

## 3. 错误处理

使用自定义异常类，由全局异常处理器映射到 HTTP 状态码：

| 异常类 | HTTP 状态码 | 场景 |
|--------|------------|------|
| `NotFoundError` | 404 | 资源不存在 |
| `ConflictError` | 409 | 唯一约束冲突（重复用户名等） |
| `ValidationError` | 422 | 业务规则校验失败 |

**禁止**在 Service 层直接 `raise HTTPException`——统一抛自定义异常，由 `main.py` 注册的 handler 转换。

## 4. 数据库会话管理

- 使用 SQLAlchemy async session
- 通过 FastAPI Depends 注入：`Annotated[AsyncSession, Depends(get_db)]`
- Service 函数接收 `session: AsyncSession` 作为第一个参数
- 事务由 session 自动管理，Service 层不手动 commit/rollback

## 5. 测试规范

- 测试文件放在 `tests/` 目录，命名 `test_{resource}.py`
- 共享 fixture 放在 `tests/conftest.py`
- 使用 `pytest.mark.parametrize` 覆盖边界 case
- 异步测试使用 `pytest-asyncio`，`asyncio_mode = "auto"`
- 每个资源至少覆盖：创建成功、查询成功、更新成功、删除成功、不存在时的错误、唯一约束冲突

## 6. 类型注解

- 所有函数参数和返回值必须有类型注解
- 使用 `from __future__ import annotations` 启用延迟求值
- Optional 字段使用 `X | None`，不使用 `Optional[X]`

## 7. 文件组织

- 每个资源一个文件（不合并多个资源到同一文件）
- `__init__.py` 保持为空或仅做 re-export
- 配置使用 Pydantic Settings，从环境变量加载
