"""Being a context is a declaration on an app, not a property of the class.

Declaring a class as a context writes nothing on it: the app's registry records
the name the agent keeps it under and the locks its use requires. So one class
can be a context of two apps under different names and locks, and an app that
merges another app's registry takes its contexts with it.
"""

import pytest

from arkitekt_spec.declare.actors.actify import derive_implementation_details
from arkitekt_spec.declare.actors.types import RegisterConfig
from arkitekt_spec.declare.agents.context import is_context, prepare_context_variables
from arkitekt_spec.declare.app import AppRegistry
from arkitekt_spec.declare.errors import RegistryFrozenError


class Session:
    """Something an agent holds for its lifetime."""


def test_declaring_a_context_leaves_the_class_alone() -> None:
    registry = AppRegistry()
    assert registry.context(Session) is Session

    assert not any(name.startswith("__rekuest") for name in vars(Session))
    assert registry.structure_registry.context_for(Session).name == "session"
    assert registry.structure_registry.context_for(Session).locks == ()


def test_name_and_locks_are_what_the_app_declared() -> None:
    registry = AppRegistry()
    registry.context(name="CameraSession", locks=["camera"])(Session)

    declared = registry.structure_registry.context_for(Session)
    assert (declared.name, declared.locks) == ("camera_session", ("camera",))


def test_one_class_is_a_context_of_two_apps_under_each_apps_rules() -> None:
    a, b = AppRegistry(), AppRegistry()
    a.context(Session, locks=["a"])
    b.context(Session, name="other", locks=["b"])

    assert a.snapshot().structure_registry.context_for(Session).locks == ("a",)
    assert b.snapshot().structure_registry.context_for(Session).name == "other"


def test_without_a_registry_nothing_is_a_context() -> None:
    assert not is_context(Session, None)
    assert not is_context(Session, AppRegistry().structure_registry)

    def use(session: Session) -> None:
        """Use."""

    variables, _ = prepare_context_variables(use)
    assert variables.context_variables == {}


def test_a_functions_context_parameters_carry_the_apps_locks() -> None:
    registry = AppRegistry()
    registry.context(Session, locks=["camera", "stage"])

    def use(session: Session, n: int) -> Session:
        """Use."""
        return session

    variables, returns = prepare_context_variables(use, registry.structure_registry)
    assert variables.context_variables == {"session": "session"}
    assert variables.required_context_locks == {"session": ["camera", "stage"]}
    assert returns.context_returns == {0: "session"}

    details = derive_implementation_details(use, RegisterConfig(auto_locks=True), registry.structure_registry)
    assert details.locks == ["camera", "stage"]


def test_a_merged_registry_brings_its_contexts() -> None:
    package, app = AppRegistry(), AppRegistry()
    package.context(Session, locks=["x"])

    app.merge(package)

    assert app.structure_registry.context_for(Session).locks == ("x",)


def test_an_unknown_class_is_named_in_the_error() -> None:
    with pytest.raises(KeyError, match="Session.*@app.context"):
        AppRegistry().structure_registry.context_for(Session)


def test_a_frozen_registry_refuses_a_context() -> None:
    registry = AppRegistry()
    registry.freeze()

    with pytest.raises(RegistryFrozenError):
        registry.context(Session)


# --------------------------------------------------------------------------- #
# The app context: the object the caller hands to run(context=...)
# --------------------------------------------------------------------------- #


class UserContext:
    """What a caller passes to ``run(context=...)``."""


def test_declaring_the_app_context_leaves_the_class_alone() -> None:
    registry = AppRegistry()
    assert registry.app_context(UserContext) is UserContext

    assert not any(name.startswith("__rekuest") for name in vars(UserContext))
    assert registry.structure_registry.is_app_context(UserContext)
    assert registry.structure_registry.app_contexts[UserContext] == "UserContext"
    assert not AppRegistry().structure_registry.is_app_context(UserContext)


def test_a_hook_is_handed_the_app_context_and_checks_its_kind() -> None:
    from arkitekt_spec.declare.agents.errors import StateRequirementsNotMet
    from arkitekt_spec.declare.agents.hooks.variables import WithVariables

    registry = AppRegistry()
    registry.app_context(UserContext)

    def hook(user: UserContext) -> None:
        """Hook."""

    variables = WithVariables(hook, registry.structure_registry)
    assert variables.app_context_variables.app_context_variables == {"user": UserContext}

    given = UserContext()
    assert variables.get_kwargs({}, {}, given) == {"user": given}
    with pytest.raises(StateRequirementsNotMet, match="UserContext"):
        variables.get_kwargs({}, {}, object())
    with pytest.raises(StateRequirementsNotMet, match="UserContext"):
        variables.get_kwargs({}, {}, None)


def test_a_snapshot_and_a_merge_carry_the_app_context() -> None:
    package, app = AppRegistry(), AppRegistry()
    package.app_context(UserContext)
    app.merge(package)

    assert app.snapshot().structure_registry.is_app_context(UserContext)
