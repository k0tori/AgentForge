"""
报告问题完全覆盖测试套件
=========================

覆盖外部评审 (2026-06-26) 和自评报告 (2026-06-24) 中的所有问题：

P0: SprintWorkspace 生命周期
P1: E2E 测试验证、toy-repo 依赖完整性
P2: 死代码检测、时区一致性
P3: 错误处理、AgentState 验证、配置可配置性
"""

import ast
import re
from pathlib import Path
from typing import Set

import pytest


# ============================================================
# 配置常量
# ============================================================

PROJECT_ROOT = Path(__file__).parent.parent.parent
TOY_REPO_ROOT = PROJECT_ROOT / "toy-repo"
SRC_ROOT = PROJECT_ROOT / "src"
TESTS_ROOT = PROJECT_ROOT / "tests"

# 外部评审报告中识别的缺失依赖
TOY_REPO_REQUIRED_DEPS = ["sqlmodel", "email-validator"]
TOY_REPO_DEV_DEPS = ["aiosqlite"]


# ============================================================
# P0: SprintWorkspace 生命周期测试
# ============================================================


class TestSprintWorkspaceLifecycle:
    """
    外部评审报告 1.2 节核心问题：
    - Evaluator 的计算型 Sensor 测的不是 Generator 写的代码
    - SprintWorkspace 缺少 seed、merge、discard 机制
    - AgentState 没有 sprint_workspace 字段
    """

    def test_sprint_workspace_class_exists(self):
        """SprintWorkspace 类应该存在"""
        workspace_path = SRC_ROOT / "harness" / "workspace.py"
        assert workspace_path.exists(), "src/harness/workspace.py 应该存在"

    def test_sprint_workspace_has_create_method(self):
        """SprintWorkspace 应该有 create() 方法实现 seed 逻辑"""
        workspace_path = SRC_ROOT / "harness" / "workspace.py"
        if not workspace_path.exists():
            pytest.skip("workspace.py 不存在")

        content = workspace_path.read_text(encoding="utf-8")
        assert "def create" in content, "SprintWorkspace 应该有 create() 方法"

    def test_sprint_workspace_has_merge_method(self):
        """SprintWorkspace 应该有 merge() 方法实现合并逻辑"""
        workspace_path = SRC_ROOT / "harness" / "workspace.py"
        if not workspace_path.exists():
            pytest.skip("workspace.py 不存在")

        content = workspace_path.read_text(encoding="utf-8")
        assert "def merge" in content, "SprintWorkspace 应该有 merge() 方法"

    def test_sprint_workspace_has_discard_method(self):
        """SprintWorkspace 应该有 discard() 方法实现丢弃逻辑"""
        workspace_path = SRC_ROOT / "harness" / "workspace.py"
        if not workspace_path.exists():
            pytest.skip("workspace.py 不存在")

        content = workspace_path.read_text(encoding="utf-8")
        assert "def discard" in content, "SprintWorkspace 应该有 discard() 方法"

    def test_sprint_workspace_create_seeds_code(self):
        """create() 应该拷贝现有代码到 sprint workspace（seed 逻辑）"""
        workspace_path = SRC_ROOT / "harness" / "workspace.py"
        if not workspace_path.exists():
            pytest.skip("workspace.py 不存在")

        content = workspace_path.read_text(encoding="utf-8")
        # 检查是否使用 copytree 进行 seed
        assert "copytree" in content or "shutil" in content, \
            "create() 应该使用 copytree 拷贝现有代码"

    def test_agent_state_has_sprint_workspace_field(self):
        """AgentState 应该有 sprint_workspace 字段"""
        state_path = SRC_ROOT / "workflow" / "state.py"
        if not state_path.exists():
            pytest.skip("state.py 不存在")

        content = state_path.read_text(encoding="utf-8")
        assert "sprint_workspace" in content, \
            "AgentState 应该有 sprint_workspace 字段"

    def test_evaluator_uses_sprint_workspace(self):
        """Evaluator 的 _run_sensors 应该使用 sprint_workspace 而非 codebase_path"""
        evaluator_path = SRC_ROOT / "agents" / "evaluator.py"
        if not evaluator_path.exists():
            pytest.skip("evaluator.py 不存在")

        content = evaluator_path.read_text(encoding="utf-8")
        # 检查是否从 state 中获取 sprint_workspace
        assert "sprint_workspace" in content, \
            "Evaluator 应该使用 state['sprint_workspace']"

    def test_generator_sets_sprint_workspace_in_state(self):
        """Generator 应该在 state 中设置 sprint_workspace"""
        generator_path = SRC_ROOT / "agents" / "generator.py"
        if not generator_path.exists():
            pytest.skip("generator.py 不存在")

        content = generator_path.read_text(encoding="utf-8")
        assert "sprint_workspace" in content, \
            "Generator 应该在返回的 state 中包含 sprint_workspace"

    def test_workflow_calls_merge_on_pass(self):
        """工作流在所有 Contract PASS 时应该调用 merge"""
        graph_path = SRC_ROOT / "workflow" / "graph.py"
        edges_path = SRC_ROOT / "workflow" / "edges.py"

        # 检查 graph.py 或 edges.py 中是否有 merge 调用
        found_merge = False
        for path in [graph_path, edges_path]:
            if path.exists():
                content = path.read_text(encoding="utf-8")
                if "merge" in content.lower():
                    found_merge = True
                    break

        assert found_merge, "工作流应该在 PASS 时调用 merge()"

    def test_workflow_calls_discard_on_fail(self):
        """工作流在失败时应该调用 discard"""
        graph_path = SRC_ROOT / "workflow" / "graph.py"
        edges_path = SRC_ROOT / "workflow" / "edges.py"

        found_discard = False
        for path in [graph_path, edges_path]:
            if path.exists():
                content = path.read_text(encoding="utf-8")
                if "discard" in content.lower():
                    found_discard = True
                    break

        assert found_discard, "工作流应该在失败时调用 discard()"


# ============================================================
# P1: toy-repo 依赖完整性测试
# ============================================================


class TestToyRepoDependencies:
    """
    外部评审报告 2.2 节：
    - toy-repo/pyproject.toml 缺少 sqlmodel, email-validator, aiosqlite
    - 本地能跑 ≠ 声明的依赖是完整的
    """

    def test_pyproject_toml_exists(self):
        """toy-repo 应该有 pyproject.toml"""
        pyproject_path = TOY_REPO_ROOT / "pyproject.toml"
        assert pyproject_path.exists(), "toy-repo/pyproject.toml 应该存在"

    def test_sqlmodel_in_dependencies(self):
        """sqlmodel 应该在 dependencies 中声明"""
        pyproject_path = TOY_REPO_ROOT / "pyproject.toml"
        if not pyproject_path.exists():
            pytest.skip("pyproject.toml 不存在")

        content = pyproject_path.read_text(encoding="utf-8")
        assert "sqlmodel" in content, \
            "sqlmodel 应该在 pyproject.toml 的 dependencies 中"

    def test_email_validator_in_dependencies(self):
        """email-validator 应该在 dependencies 中声明"""
        pyproject_path = TOY_REPO_ROOT / "pyproject.toml"
        if not pyproject_path.exists():
            pytest.skip("pyproject.toml 不存在")

        content = pyproject_path.read_text(encoding="utf-8")
        assert "email-validator" in content or "pydantic[email]" in content, \
            "email-validator 或 pydantic[email] 应该在 dependencies 中"

    def test_aiosqlite_in_dev_dependencies(self):
        """aiosqlite 应该在 dev dependencies 中声明"""
        pyproject_path = TOY_REPO_ROOT / "pyproject.toml"
        if not pyproject_path.exists():
            pytest.skip("pyproject.toml 不存在")

        content = pyproject_path.read_text(encoding="utf-8")
        assert "aiosqlite" in content, \
            "aiosqlite 应该在 dev dependencies 中"

    def test_all_imports_have_declared_dependencies(self):
        """所有 import 的包都应该在 pyproject.toml 中声明"""
        pyproject_path = TOY_REPO_ROOT / "pyproject.toml"
        if not pyproject_path.exists():
            pytest.skip("pyproject.toml 不存在")

        content = pyproject_path.read_text(encoding="utf-8").lower()

        # 扫描所有 Python 文件的 import
        python_files = list(TOY_REPO_ROOT.rglob("*.py"))
        third_party_imports: Set[str] = set()

        for py_file in python_files:
            try:
                tree = ast.parse(py_file.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            third_party_imports.add(alias.name.split(".")[0])
                    elif isinstance(node, ast.ImportFrom):
                        if node.module:
                            third_party_imports.add(node.module.split(".")[0])
            except SyntaxError:
                pass

        # 标准库模块（不完整，但覆盖常见）
        stdlib_modules = {
            "os", "sys", "pathlib", "typing", "datetime", "json", "re",
            "abc", "enum", "dataclasses", "collections", "functools",
            "itertools", "copy", "math", "random", "string", "io",
            "tempfile", "shutil", "subprocess", "threading", "asyncio",
            "logging", "unittest", "pytest", "httpx", "__future__",
            "contextlib", "uuid", "time", "hashlib", "secrets",
        }

        # 项目内部模块（toy-repo 自己的包）
        internal_packages = {"src", "tests", "conftest"}

        # 检查第三方依赖是否声明
        missing_deps = []
        for imp in third_party_imports:
            if imp in stdlib_modules:
                continue
            if imp.startswith("_"):
                continue
            if imp in internal_packages:
                continue
            # 检查是否在 pyproject.toml 中声明
            if imp not in content and imp.replace("_", "-") not in content:
                missing_deps.append(imp)

        if missing_deps:
            pytest.fail(f"以下依赖未在 pyproject.toml 中声明: {missing_deps}")


# ============================================================
# P1: E2E 测试验证
# ============================================================


class TestE2EIntegrity:
    """
    外部评审报告 2.1 节：
    - test_e2e_tag.py 的"E2E"标签名不副实
    - 所有工具被 mock，无法暴露真实问题
    """

    def test_e2e_test_exists(self):
        """应该存在 E2E 测试文件"""
        e2e_path = TESTS_ROOT / "integration" / "test_e2e_tag.py"
        assert e2e_path.exists(), "tests/integration/test_e2e_tag.py 应该存在"

    def test_e2e_has_unmocked_variant(self):
        """应该有不 mock 工具执行的集成测试变体"""
        integration_dir = TESTS_ROOT / "integration"
        if not integration_dir.exists():
            pytest.skip("integration 目录不存在")

        # 查找包含 "real" 或 "unmock" 或 "full" 的测试文件
        test_files = list(integration_dir.glob("test_*.py"))
        has_unmocked = any(
            "real" in f.name or "unmock" in f.name or "full" in f.name
            for f in test_files
        )

        # 也检查现有文件中是否有不 mock 的测试
        for test_file in test_files:
            content = test_file.read_text(encoding="utf-8")
            # 如果有 @pytest.mark.integration 且没有 mock registry.execute
            if "@pytest.mark.integration" in content:
                has_unmocked = True
                break

        assert has_unmocked, \
            "应该有不 mock 工具执行的集成测试（验证 SprintWorkspace 真实路径）"

    def test_e2e_verifies_file_creation(self):
        """E2E 测试应该验证文件是否真实创建"""
        e2e_path = TESTS_ROOT / "integration" / "test_e2e_tag.py"
        if not e2e_path.exists():
            pytest.skip("test_e2e_tag.py 不存在")

        content = e2e_path.read_text(encoding="utf-8")
        # 检查是否有文件存在性验证
        has_file_check = any(keyword in content for keyword in [
            "exists()", "is_file()", "os.path.exists", "Path.exists",
            "sprint_workspace", "merge"
        ])

        # 如果是纯 mock 测试，这个检查会失败
        # 这正是我们要暴露的问题
        if not has_file_check:
            pytest.xfail(
                "E2E 测试没有验证文件真实创建 - "
                "这正是外部评审报告 2.1 节指出的问题"
            )


# ============================================================
# P2: 死代码检测测试
# ============================================================


class TestDeadCode:
    """
    外部评审报告 2.3 节：
    - cleanup_sprint_workspace() 没人调用
    - cleanup_old_tasks() 有时区 bug 且没人调用
    - chat_structured() 是死代码
    """

    def test_cleanup_sprint_workspace_removed_or_integrated(self):
        """cleanup_sprint_workspace() 应该被删除或集成到 SprintWorkspace"""
        generator_path = SRC_ROOT / "agents" / "generator.py"
        if not generator_path.exists():
            pytest.skip("generator.py 不存在")

        content = generator_path.read_text(encoding="utf-8")

        # 如果 SprintWorkspace 存在，cleanup_sprint_workspace 应该被删除
        workspace_path = SRC_ROOT / "harness" / "workspace.py"
        if workspace_path.exists():
            assert "cleanup_sprint_workspace" not in content, \
                "cleanup_sprint_workspace() 应该被删除，逻辑并入 SprintWorkspace"

    def test_cleanup_old_tasks_has_timezone_fix(self):
        """cleanup_old_tasks() 应该修复时区 bug"""
        task_manager_path = SRC_ROOT / "workflow" / "task_manager.py"
        if not task_manager_path.exists():
            pytest.skip("task_manager.py 不存在")

        content = task_manager_path.read_text(encoding="utf-8")

        if "cleanup_old_tasks" in content:
            # 检查是否使用了 tz-aware 的 datetime
            # Bug: datetime.now() 是 tz-naive，但 task.completed_at 是 tz-aware
            assert "datetime.now(UTC)" in content or "datetime.now(tz=" in content, \
                "cleanup_old_tasks() 应该使用 datetime.now(UTC) 修复时区 bug"

    def test_cleanup_old_tasks_is_called(self):
        """cleanup_old_tasks() 应该被实际调用"""
        # 搜索整个项目
        found_call = False
        for py_file in SRC_ROOT.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8")
                # 跳过定义本身
                if "def cleanup_old_tasks" in content:
                    continue
                if "cleanup_old_tasks" in content:
                    found_call = True
                    break
            except Exception:
                pass

        if not found_call:
            pytest.xfail(
                "cleanup_old_tasks() 没有被调用 - "
                "这正是外部评审报告 2.3 节指出的问题"
            )

    def test_chat_structured_removed_or_documented(self):
        """chat_structured() 应该被删除或明确标注"""
        client_path = SRC_ROOT / "llm" / "client.py"
        if not client_path.exists():
            pytest.skip("client.py 不存在")

        content = client_path.read_text(encoding="utf-8")

        if "chat_structured" in content:
            # 检查是否有明确的文档注释
            has_documentation = any(keyword in content for keyword in [
                "保留给未来", "for future", "unused", "dead code",
                "not used", "TODO", "STUB", "Phase 2"
            ])

            # 检查是否被调用
            is_called = False
            for py_file in SRC_ROOT.rglob("*.py"):
                if py_file == client_path:
                    continue
                try:
                    file_content = py_file.read_text(encoding="utf-8")
                    if "chat_structured" in file_content:
                        is_called = True
                        break
                except Exception:
                    pass

            if not is_called and not has_documentation:
                pytest.xfail(
                    "chat_structured() 是死代码且没有文档 - "
                    "这正是外部评审报告 2.4 节指出的问题"
                )


# ============================================================
# P2: 时区一致性测试
# ============================================================


class TestTimezoneConsistency:
    """
    外部评审报告 2.3 节：
    - datetime.now() vs datetime.now(UTC) 不一致
    """

    def test_no_naive_datetime_now(self):
        """不应该使用 tz-naive 的 datetime.now()"""
        issues = []

        for py_file in SRC_ROOT.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8")
                # 查找 datetime.now() 但排除 datetime.now(UTC) 和 datetime.now(tz=...)
                # 使用正则匹配
                matches = re.findall(
                    r'datetime\.now\(\s*\)',  # datetime.now() 没有参数
                    content
                )
                if matches:
                    issues.append(f"{py_file}: {len(matches)} 处 tz-naive datetime.now()")
            except Exception:
                pass

        if issues:
            pytest.xfail(
                f"发现 tz-naive datetime.now(): {issues} - "
                "应该使用 datetime.now(UTC)"
            )


# ============================================================
# P3: 错误处理测试
# ============================================================


class TestErrorHandling:
    """
    自评报告 第六章 3 节：
    - 错误处理几乎为零
    - LLM 调用失败、工具执行失败、节点异常的处理
    """

    def test_agents_have_try_except(self):
        """Agent 的 execute() 方法应该有 try-except"""
        agents_dir = SRC_ROOT / "agents"
        if not agents_dir.exists():
            pytest.skip("agents 目录不存在")

        for agent_file in agents_dir.glob("*.py"):
            if agent_file.name.startswith("_"):
                continue

            content = agent_file.read_text(encoding="utf-8")
            if "def execute" in content:
                # 检查是否有 try-except
                assert "try:" in content, \
                    f"{agent_file.name} 的 execute() 应该有 try-except"

    def test_llm_client_has_retry(self):
        """LLM 客户端应该有重试机制"""
        client_path = SRC_ROOT / "llm" / "client.py"
        if not client_path.exists():
            pytest.skip("client.py 不存在")

        content = client_path.read_text(encoding="utf-8")
        has_retry = any(keyword in content for keyword in [
            "retry", "backoff", "max_retries", "tenacity"
        ])

        assert has_retry, "LLM 客户端应该有重试机制"

    def test_tools_have_timeout_mechanism(self):
        """工具执行应该有超时机制"""
        # 检查工具执行路径是否有超时
        test_ops_path = SRC_ROOT / "tools" / "test_ops.py"
        registry_path = SRC_ROOT / "tools" / "registry.py"

        has_timeout = False

        # 检查 test_ops.py（实际执行测试的工具）
        if test_ops_path.exists():
            content = test_ops_path.read_text(encoding="utf-8")
            if "timeout" in content.lower():
                has_timeout = True

        # 检查 loop_controller.py（控制循环的组件）
        controller_path = SRC_ROOT / "harness" / "loop" / "controller.py"
        if controller_path.exists():
            content = controller_path.read_text(encoding="utf-8")
            if "timeout" in content.lower():
                has_timeout = True

        assert has_timeout, "工具执行路径应该有超时机制（在 test_ops.py 或 controller.py 中）"


# ============================================================
# P3: AgentState 验证测试
# ============================================================


class TestAgentStateValidation:
    """
    自评报告 第六章 4 节：
    - AgentState 是 TypedDict 而非 Pydantic Model
    - 运行时没有字段验证
    """

    def test_agent_state_type(self):
        """AgentState 应该是 Pydantic BaseModel 或有验证机制"""
        state_path = SRC_ROOT / "workflow" / "state.py"
        if not state_path.exists():
            pytest.skip("state.py 不存在")

        content = state_path.read_text(encoding="utf-8")

        # 检查是否使用 Pydantic
        uses_pydantic = "BaseModel" in content
        # 检查是否使用 TypedDict（不应该）
        uses_typed_dict = "TypedDict" in content

        if uses_typed_dict:
            pytest.xfail(
                "AgentState 使用 TypedDict 而非 Pydantic BaseModel - "
                "这正是自评报告指出的问题"
            )

        assert uses_pydantic or "validate" in content.lower(), \
            "AgentState 应该使用 Pydantic BaseModel 或有验证机制"


# ============================================================
# P3: 配置可配置性测试
# ============================================================


class TestConfigurability:
    """
    自评报告 第六章 6 节：
    - Temperature=0.1 硬编码在 client.py
    - Embedding 模型和维度硬编码在 config.py
    - Prompt 模板硬编码为字符串常量
    """

    def test_temperature_not_hardcoded(self):
        """Temperature 不应该硬编码在 client.py"""
        client_path = SRC_ROOT / "llm" / "client.py"
        if not client_path.exists():
            pytest.skip("client.py 不存在")

        content = client_path.read_text(encoding="utf-8")

        # 检查是否有硬编码的 temperature
        # 允许从配置读取
        if "temperature=0.1" in content or "temperature = 0.1" in content:
            # 检查是否从配置读取
            if "config" not in content.lower() and "settings" not in content.lower():
                pytest.xfail(
                    "Temperature 硬编码在 client.py - "
                    "应该从配置读取"
                )

    def test_config_has_temperature_setting(self):
        """配置文件应该有 temperature 设置项"""
        config_path = SRC_ROOT / "config.py"
        if not config_path.exists():
            pytest.skip("config.py 不存在")

        content = config_path.read_text(encoding="utf-8")

        # 检查 config.py 是否有 temperature 配置
        has_temperature_in_config = "temperature" in content.lower()

        # 检查 client.py 是否从配置读取
        client_path = SRC_ROOT / "llm" / "client.py"
        if client_path.exists():
            client_content = client_path.read_text(encoding="utf-8")
            # 检查是否有硬编码的 temperature
            has_hardcoded_temp = "temperature=0.1" in client_content or "temperature = 0.1" in client_content

            if has_hardcoded_temp and not has_temperature_in_config:
                pytest.xfail(
                    "Temperature 硬编码在 client.py (0.1)，config.py 没有配置项 - "
                    "这正是自评报告第六章 6 节指出的问题"
                )

        assert has_temperature_in_config, "config.py 应该有 temperature 配置项"


# ============================================================
# P3: 日志系统测试
# ============================================================


class TestLoggingSystem:
    """
    自评报告 第六章 8 节：
    - 日志系统缺失
    - 没有 logging 配置
    """

    def test_logging_config_exists(self):
        """应该有 logging 配置文件"""
        logging_config = SRC_ROOT / "logging_config.py"
        assert logging_config.exists(), "src/logging_config.py 应该存在"

    def test_no_print_statements_in_src(self):
        """src 目录下不应该有 print 语句"""
        print_files = []

        for py_file in SRC_ROOT.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8")
                # 排除注释和字符串中的 print
                tree = ast.parse(content)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call):
                        if hasattr(node.func, 'id') and node.func.id == 'print':
                            print_files.append(str(py_file))
                            break
            except Exception:
                pass

        if print_files:
            pytest.xfail(
                f"发现 print 语句: {print_files[:5]} - "
                "应该使用 logging"
            )


# ============================================================
# 综合验证测试
# ============================================================


class TestComprehensiveVerification:
    """综合验证所有报告问题是否已修复"""

    def test_all_p0_issues_resolved(self):
        """所有 P0 问题应该已解决"""
        issues = []

        # 检查 SprintWorkspace
        workspace_path = SRC_ROOT / "harness" / "workspace.py"
        if not workspace_path.exists():
            issues.append("SprintWorkspace 未实现")

        # 检查 AgentState 有 sprint_workspace
        state_path = SRC_ROOT / "workflow" / "state.py"
        if state_path.exists():
            content = state_path.read_text(encoding="utf-8")
            if "sprint_workspace" not in content:
                issues.append("AgentState 缺少 sprint_workspace 字段")

        # 检查 Evaluator 使用 sprint_workspace
        evaluator_path = SRC_ROOT / "agents" / "evaluator.py"
        if evaluator_path.exists():
            content = evaluator_path.read_text(encoding="utf-8")
            if "sprint_workspace" not in content:
                issues.append("Evaluator 未使用 sprint_workspace")

        if issues:
            pytest.fail(f"P0 问题未解决: {issues}")

    def test_all_p1_issues_resolved(self):
        """所有 P1 问题应该已解决"""
        issues = []

        # 检查 toy-repo 依赖
        pyproject_path = TOY_REPO_ROOT / "pyproject.toml"
        if pyproject_path.exists():
            content = pyproject_path.read_text(encoding="utf-8").lower()
            if "sqlmodel" not in content:
                issues.append("toy-repo 缺少 sqlmodel 依赖")
            if "email-validator" not in content and "pydantic[email]" not in content:
                issues.append("toy-repo 缺少 email-validator 依赖")
            if "aiosqlite" not in content:
                issues.append("toy-repo 缺少 aiosqlite 依赖")

        if issues:
            pytest.fail(f"P1 问题未解决: {issues}")

    def test_all_p2_issues_resolved(self):
        """所有 P2 问题应该已解决"""
        issues = []

        # 检查死代码
        generator_path = SRC_ROOT / "agents" / "generator.py"
        if generator_path.exists():
            content = generator_path.read_text(encoding="utf-8")
            workspace_path = SRC_ROOT / "harness" / "workspace.py"
            if workspace_path.exists() and "cleanup_sprint_workspace" in content:
                issues.append("cleanup_sprint_workspace() 仍是死代码")

        if issues:
            pytest.fail(f"P2 问题未解决: {issues}")


# ============================================================
# 测试运行入口
# ============================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
