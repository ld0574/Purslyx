"""把浏览器脚本的 Node 单测纳入统一 pytest 门禁。"""

from __future__ import annotations

import subprocess
from pathlib import Path


def test_userscript_node_suite() -> None:
    project_dir = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        ["node", "--test", "tests/userscript/userscript.test.mjs"],
        cwd=project_dir,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
