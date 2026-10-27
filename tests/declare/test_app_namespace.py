"""A derived identifier is in the app's namespace only for a class the app defines itself.

The same app gets the same identifier for its own enum whether its file is run
(``__main__``) or imported, while a library's enum keeps the identifier of its
module, so it is the same in every app that uses it. The files are real: the rule
reads where a module lives, not what it is called.
"""

import importlib
import runpy
import sys
from collections.abc import Iterator
from enum import Enum
from pathlib import Path

import pytest

from arkitekt_spec.declare.structures.convert import AppNamespace, source_root
from arkitekt_spec.declare.structures.registry import StructureRegistry

APP_SOURCE = '''
from enum import Enum


class ThresholdMethod(Enum):
    OTSU = "otsu"
    LI = "li"
'''

LIBRARY_SOURCE = '''
from enum import Enum


class ProjectionMode(Enum):
    MAX = "max"
    MEAN = "mean"
'''


def write(path: Path, source: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(source)
    return path


@pytest.fixture
def isolated_modules() -> Iterator[None]:
    """Undo what a test adds to ``sys.path`` and ``sys.modules``."""
    path, modules = list(sys.path), set(sys.modules)
    yield
    sys.path[:] = path
    for name in set(sys.modules) - modules:
        del sys.modules[name]


def registry_for(identifier: str, constructed_in: Path, module: str) -> StructureRegistry:
    """The registry of an app named ``identifier`` that ``module`` (in ``constructed_in``) constructs."""
    return StructureRegistry(
        namespace=AppNamespace(identifier=identifier, root=source_root(constructed_in), module=module)
    )


def enum_in(namespace: dict[str, object], name: str) -> type[Enum]:
    found = namespace[name]
    assert isinstance(found, type) and issubclass(found, Enum)
    return found


@pytest.mark.usefixtures("isolated_modules")
def test_an_app_local_enum_is_the_same_run_or_imported(tmp_path: Path) -> None:
    script = write(tmp_path / "apps" / "cluster_segment_path_app.py", APP_SOURCE)

    as_main = enum_in(runpy.run_path(str(script), run_name="__main__"), "ThresholdMethod")
    sys.path.insert(0, str(script.parent))
    as_import = enum_in(vars(importlib.import_module("cluster_segment_path_app")), "ThresholdMethod")
    assert (as_main.__module__, as_import.__module__) == ("__main__", "cluster_segment_path_app")

    run = registry_for("cluster-segment-path", script, "__main__")
    imported = registry_for("cluster-segment-path", script, "cluster_segment_path_app")
    assert run.derive_enum(as_main).identifier == "@cluster-segment-path/thresholdmethod"
    assert imported.derive_enum(as_import).identifier == "@cluster-segment-path/thresholdmethod"


@pytest.mark.usefixtures("isolated_modules")
def test_a_module_beside_the_app_or_in_its_package_is_the_apps(tmp_path: Path) -> None:
    write(tmp_path / "myapp" / "__init__.py", "")
    app = write(tmp_path / "myapp" / "app.py", "")
    write(tmp_path / "myapp" / "kinds" / "__init__.py", "")
    write(tmp_path / "myapp" / "kinds" / "methods.py", APP_SOURCE)
    sys.path.insert(0, str(tmp_path))

    assert source_root(app) == (tmp_path / "myapp").resolve()
    methods = enum_in(vars(importlib.import_module("myapp.kinds.methods")), "ThresholdMethod")
    assert registry_for("myapp", app, "myapp.app").derive_enum(methods).identifier == "@myapp/thresholdmethod"


@pytest.mark.usefixtures("isolated_modules")
def test_a_library_enum_keeps_its_module_identifier_in_every_app(tmp_path: Path) -> None:
    write(tmp_path / "lib" / "sharedlib" / "__init__.py", "")
    write(tmp_path / "lib" / "sharedlib" / "schema.py", LIBRARY_SOURCE)
    first = write(tmp_path / "apps" / "first_app.py", "")
    second = write(tmp_path / "other" / "second_app.py", "")
    sys.path.insert(0, str(tmp_path / "lib"))
    mode = enum_in(vars(importlib.import_module("sharedlib.schema")), "ProjectionMode")

    identifiers = {
        registry_for("first", first, "__main__").derive_enum(mode).identifier,
        registry_for("second", second, "second_app").derive_enum(mode).identifier,
        StructureRegistry().derive_enum(mode).identifier,
    }
    assert identifiers == {"@sharedlib.schema/projectionmode"}


@pytest.mark.usefixtures("isolated_modules")
def test_a_virtualenv_inside_the_app_directory_is_not_the_apps(tmp_path: Path) -> None:
    app = write(tmp_path / "testing" / "random_image_app.py", "")
    installed = tmp_path / "testing" / ".venv" / "lib" / "python3.12" / "site-packages"
    write(installed / "sharedlib" / "__init__.py", LIBRARY_SOURCE)
    sys.path.insert(0, str(installed))
    mode = enum_in(vars(importlib.import_module("sharedlib")), "ProjectionMode")

    registry = registry_for("random-image-generator", app, "__main__")
    assert registry.derive_enum(mode).identifier == "@sharedlib/projectionmode"


@pytest.mark.usefixtures("isolated_modules")
def test_an_installed_app_is_still_its_own(tmp_path: Path) -> None:
    """An app deployed with ``pip install .`` lives in site-packages: below its own root
    nothing is an installed directory, so its classes stay the app's."""
    installed = tmp_path / "venv" / "lib" / "python3.12" / "site-packages"
    write(installed / "myapp" / "__init__.py", "")
    app = write(installed / "myapp" / "app.py", "")
    write(installed / "myapp" / "kinds.py", APP_SOURCE)
    sys.path.insert(0, str(installed))
    methods = enum_in(vars(importlib.import_module("myapp.kinds")), "ThresholdMethod")

    assert registry_for("myapp", app, "myapp.app").derive_enum(methods).identifier == "@myapp/thresholdmethod"


def test_a_console_app_owns_only_main() -> None:
    """Code typed into a console has no file, so only ``__main__`` is the app's."""
    namespace = AppNamespace(identifier="console", root=source_root(Path("<stdin>")), module="__main__")
    assert namespace.root is None
    as_main = Enum("Method", {"OTSU": "otsu"}, module="__main__")
    assert namespace.of(as_main) == "console"
    assert namespace.of(Path) is None


def test_a_model_and_a_memory_structure_follow_the_same_rule(tmp_path: Path) -> None:
    app = write(tmp_path / "apps" / "app.py", "")
    registry = registry_for("myapp", app, "app")

    class Blob:
        pass

    Blob.__module__ = "__main__"
    assert registry.register_as_memory_structure(Blob).identifier == "@myapp/blob"
    # A library class: pathlib is not under the app's directory.
    assert registry.register_as_memory_structure(Path).identifier == "@pathlib/path"
    assert registry.copy_maps().namespace == registry.namespace
