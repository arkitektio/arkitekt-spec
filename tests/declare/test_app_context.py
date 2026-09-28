"""An app declares at most one class as its app context, and a run must hand it one.

That the agent enforces it is tested in arkitekt-runtime.
"""

import pytest

from arkitekt_spec.declare.app import AppRegistry
from arkitekt_spec.declare.errors import AppContextError


class Config:
    def __init__(self, label: str = "cfg") -> None:
        self.label = label


class OtherConfig:
    pass


def test_an_app_declares_one_app_context_class() -> None:
    registry = AppRegistry()
    assert registry.app_context_class is None

    registry.app_context(Config)
    assert registry.app_context_class is Config
    registry.app_context(Config)  # again: nothing happens

    with pytest.raises(ValueError, match="already declares Config"):
        registry.app_context(OtherConfig)


def test_merging_two_different_declarations_is_refused() -> None:
    app, package = AppRegistry(), AppRegistry()
    app.app_context(Config)
    package.app_context(OtherConfig)

    with pytest.raises(ValueError, match="already declares Config"):
        app.merge(package)

    same = AppRegistry()
    same.app_context(Config)
    app.merge(same)
    assert app.app_context_class is Config


def test_require_app_context_checks_what_the_run_passed() -> None:
    registry = AppRegistry()
    registry.require_app_context(None, whose="App 'x'")
    with pytest.raises(AppContextError, match="declares no app context, but was given a Config"):
        registry.require_app_context(Config(), whose="App 'x'")

    registry.app_context(Config)
    registry.require_app_context(Config(), whose="App 'x'")
    with pytest.raises(AppContextError, match="declares an app context of class Config, but none was given"):
        registry.require_app_context(None, whose="App 'x'")
    with pytest.raises(AppContextError, match="but was given a OtherConfig"):
        registry.require_app_context(OtherConfig(), whose="App 'x'")
