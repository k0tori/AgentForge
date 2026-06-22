from __future__ import annotations

from src.harness.safety.annotations import TOOL_ANNOTATIONS


def validate_tool_set(tool_names: list[str]) -> list[str]:
    """Validate a tool set against Rule of Two constraints.

    Rule of Two: A component with two high-risk attributes must not have a third.
    Generator has: (1) access to private code, (2) processes potentially untrusted RAG content.
    Therefore: (3) network egress / external state mutation must be excluded.

    Returns a list of violation descriptions. Empty list means the tool set is safe.
    """
    violations: list[str] = []

    for name in tool_names:
        annotation = TOOL_ANNOTATIONS.get(name)
        if annotation is None:
            violations.append(f"Tool '{name}' has no annotations defined")
            continue

        # Violation: tool has both openWorld and write capabilities
        if annotation.get("openWorldHint") and not annotation.get("readOnlyHint"):
            violations.append(
                f"Tool '{name}' has openWorld=true AND readOnly=false — "
                f"violates Rule of Two (network egress + write capability)"
            )

        # Flag: tool name suggests network capability
        network_patterns = ("http", "fetch", "send", "request", "curl", "wget", "socket")
        if any(pattern in name.lower() for pattern in network_patterns):
            violations.append(
                f"Tool '{name}' name suggests network capability — "
                f"verify it has openWorldHint=false"
            )

    return violations
