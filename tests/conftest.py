"""Fast tests opt into host execution on purpose.

Production defaults to SANDBOX_MODE=docker and refuses to run generated code
without a container. This file does not apply when the variable is already set,
so `SANDBOX_MODE=docker pytest -m sandbox` still exercises real containers.
"""
from __future__ import annotations

import os

os.environ.setdefault("SANDBOX_MODE", "host")
