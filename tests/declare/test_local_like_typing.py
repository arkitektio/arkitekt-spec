"""``Local.like`` takes a registered action of any signature, under basedpyright strict."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SNIPPET = '''
from arkitekt_spec.declare.app import AppRegistry
from arkitekt_spec.declare.blok.declarations import Local

registry = AppRegistry()


@registry.register_action
def deconvolve(iterations: int = 15, mode: str = "fast") -> float:
    """Deconvolve."""
    return 0.0


by_function: Local[object] = Local.like(deconvolve, "iterations", persist=True)
by_interface: Local[object] = Local.like("deconvolve", "mode", default="slow")
'''


def test_local_like_takes_a_decorated_action_under_strict(tmp_path: Path) -> None:
    pyright = shutil.which("basedpyright", path=str(Path(sys.executable).parent))
    if pyright is None:
        pytest.skip("basedpyright is not installed")
    (tmp_path / "snippet.py").write_text(SNIPPET)
    (tmp_path / "pyrightconfig.json").write_text(json.dumps({"typeCheckingMode": "strict", "include": ["snippet.py"]}))
    checked = subprocess.run([pyright, "--pythonpath", sys.executable, "--project", str(tmp_path)], capture_output=True, text=True, cwd=tmp_path, check=False)
    assert checked.returncode == 0, checked.stdout
