#!/usr/bin/env python3
"""兼容分发包入口；唯一实现位于完整 Skill 的 scripts 目录。"""

from pathlib import Path
import runpy

if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).absolute().parent / "skills/agent-collab/scripts/agent_collab.py"), run_name="__main__")
