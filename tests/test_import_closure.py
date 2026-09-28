"""The spec is what every party depends on, so it depends on none of them.

Checked in a subprocess: the test session itself may have loaded anything.
"""

import ast
import subprocess
import sys

RUNTIMES = ("rekuest", "arkitekt", "rath", "koil", "fakts", "websockets", "graphql")


def _loaded_after(statement: str) -> set[str]:
    code = f"import sys; {statement}; print(sorted({{m.split('.')[0] for m in sys.modules}}))"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    return set(ast.literal_eval(out.stdout.strip()))


def test_the_declaration_layer_loads_no_runtime():
    loaded = _loaded_after(
        "import arkitekt_spec, arkitekt_spec.declare.app, arkitekt_spec.declare.widgets"
    )
    assert not loaded & set(RUNTIMES), loaded & set(RUNTIMES)


APP = """
from arkitekt_spec.declare.app import AppRegistry

app = AppRegistry()

@app.register
def double(x: int) -> int:
    return 2 * x

@app.startup
async def boot():
    return None

app.to_implement_agent_input()
"""


def test_an_app_can_be_declared_with_the_spec_alone(tmp_path):
    script = tmp_path / "app.py"
    script.write_text(APP)
    loaded = _loaded_after(f"exec(open({str(script)!r}).read())")
    assert not loaded & set(RUNTIMES), loaded & set(RUNTIMES)
