"""Every place that states the package version agrees (pyproject, package, plugin, MCP registry entry)."""
import json
import re
from pathlib import Path

import readanydoc

ROOT = Path(__file__).resolve().parents[1]


def test_versions_agree():
    v = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M).group(1)
    server = json.loads((ROOT / "server.json").read_text())
    plugin = json.loads((ROOT / "plugins/readanydoc/.claude-plugin/plugin.json").read_text())
    assert readanydoc.__version__ == v
    assert plugin["version"] == v
    assert server["version"] == v and all(p.get("version") == v for p in server.get("packages", []))
