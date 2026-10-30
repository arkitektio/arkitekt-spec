"""User-facing annotation markers for Rekuest ports.

These are the objects a user places inside :data:`typing.Annotated` to attach
extra metadata to a port -- a description, a default value, a proposed unit set,
or a requires/provides constraint. They are picked up by the annotation parsers
in :mod:`rekuest.annotations.parsers` while a callable is being defined.

All markers share a consistent PascalCase, callable surface and are re-exported
from the top-level :mod:`rekuest` package::

    from arkitekt import Description, Default, Key, Units, Requires, Provides

    def measure(
        exposure: Annotated[Duration, Units("ms", "s"), Description("Exposure time")],
    ) -> int: ...
"""

from typing import TypeVar

from arkitekt_spec.actions import (
    ProvidesInput,
    RequiresInput,
)
from arkitekt_spec.declare.structures.types import JSONSerializable

T = TypeVar("T")


class Description:
    """Attach a human-readable description to a port.

    Use inside :data:`typing.Annotated` to override the description a port would
    otherwise take from the docstring::

        name: Annotated[str, Description("The user's full name")]
    """

    def __init__(self, value: str) -> None:
        """Initialize the Description marker with a value."""
        if not isinstance(value, str):  # type: ignore[reportUnnecessaryIsInstance]
            raise TypeError("Description value must be a string")
        self.value = value

    def __repr__(self) -> str:
        """Return a string representation of the Description marker."""
        return f"Description({self.value})"


class Key:
    """Name a return: the key it is read by, in place of ``return0``.

    Use inside :data:`typing.Annotated` on a return annotation (or on an element of a
    ``tuple[...]`` return)::

        def count(image: Image) -> Annotated[int, Key("count")]: ...

    A bloks reads it as ``<record>.returns.count``. A ``NamedTuple`` return names
    every return at once, by its fields. An argument is keyed by its parameter
    name, so ``Key`` is refused there.
    """

    def __init__(self, value: str) -> None:
        """Initialize the Key marker with the return's key."""
        if not isinstance(value, str) or not value.isidentifier():  # type: ignore[reportUnnecessaryIsInstance]
            raise TypeError(f"Key value must be an identifier, got {value!r}")
        self.value = value

    def __repr__(self) -> str:
        """Return a string representation of the Key marker."""
        return f"Key({self.value!r})"


class Default:
    """Attach a default value to a port.

    Use inside :data:`typing.Annotated` to give a port a default without adding a
    parameter default to the function signature::

        threshold: Annotated[int, Default(5)]
    """

    def __init__(self, value: JSONSerializable) -> None:
        """Initialize the Default marker with a value."""
        self.value = value

    def __repr__(self) -> str:
        """Return a string representation of the Default marker."""
        return f"Default(value={self.value})"


class Units:
    """Override the units a UI proposes for a ``QUANTITY`` port.

    Use on a kanne-typed parameter to replace the type's default proposed units::

        exposure: Annotated[Duration, Units("ms", "s")]

    Proposals only -- any unit of the same dimension is still valid input.
    """

    def __init__(self, *units: str) -> None:
        """Initialize with the units to propose (at least one)."""
        if not units:
            raise TypeError("Units requires at least one unit")
        self.units = list(units)

    def __repr__(self) -> str:
        """Return a string representation of the Units marker."""
        return f"Units({', '.join(map(repr, self.units))})"


class Requires(RequiresInput):
    """Requires class for type hinting.

    This class is used to provide a description for a required port group. It is used in the
    `requires` parameter of the `@context` decorator.
    """

    def __repr__(self) -> str:
        """Return a string representation of the Requires class.
        Returns:
            str: A string representation of the Requires class.
        """
        return f"Requires({self.key}, {self.operator}, {self.value})"


class Fits:
    """What this implementation can handle of a structure argument.

    Beside :class:`Requires`, which says what the action accepts: a ``Fits`` is about one
    deployment, and lives on its implementation, not on the action or in its hash. It is a
    formula over the argument's descriptors and the agent's hardware, parsed and never run::

        image: Annotated[Lens, Fits("@mikro/size * @mikro/dsize < agent.memory_mib * 1048576")]

    An action is not offered for an object no live implementation fits, and the agent refuses
    one that reaches it anyway.
    """

    def __init__(self, expression: str) -> None:
        """Hold the formula; :func:`~arkitekt_spec.declare.register.register_func` parses it."""
        self.expression = expression

    def __repr__(self) -> str:
        """Return a string representation of the Fits marker."""
        return f"Fits({self.expression!r})"


class Provides(ProvidesInput):
    """Provides class for type hinting.

    This class is used to provide a description for a provided port group. It is used in the
    `provides` parameter of the `@context` decorator.
    """

    def __repr__(self) -> str:
        """Return a string representation of the Provides class.
        Returns:
            str: A string representation of the Provides class.
        """
        return f"Provides({self.key}    , {self.operator}, {self.value})"
