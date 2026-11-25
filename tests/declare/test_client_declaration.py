"""A client declares itself on its class: name, image, scopes, and what its binder requires."""

from typing import Annotated, ClassVar, Optional, Self

import pytest

from arkitekt_spec.declare.client import ClientDefinitionError, client, client_of
from arkitekt_spec.declare.injections import _wants_alias
from arkitekt_spec.declare.wiring import Alias, Require, TokenLoader


def test_optional_and_pipe_none_both_read_as_an_optional_alias() -> None:
    assert _wants_alias(Optional[Alias]) == (True, True)  # noqa: UP045
    assert _wants_alias(Alias | None) == (True, True)
    assert _wants_alias(Alias) == (True, False)
    assert _wants_alias(Annotated[int, "x"]) == (False, False)


def test_a_client_says_its_name_image_scopes_and_requirements() -> None:
    @client(image="example/thing:3")
    class ThingClient:
        __scopes__: ClassVar[tuple[str, ...]] = ("thing_write", "thing_read")

        @classmethod
        def from_fakts(
            cls,
            thing: Annotated[Alias, Require("live.example.thing")],
            tokens: TokenLoader,
        ) -> Self:
            return cls()

    declared = client_of(ThingClient)
    assert declared is not None
    assert (declared.name, declared.image) == ("thing_client", "example/thing:3")
    assert declared.get_scopes() == ["thing_read", "thing_write"]
    assert [r.key for r in declared.get_requirements()] == ["thing"]
    assert declared.needs_fakts and not declared.takes_registry


def test_a_subclass_is_not_the_client_until_it_says_so() -> None:
    @client(requires={})
    class Base:
        pass

    class Sub(Base):
        pass

    assert client_of(Base) is not None
    assert client_of(Sub) is None


def test_a_client_neither_built_nor_described_is_refused() -> None:
    with pytest.raises(ClientDefinitionError, match="no binder"):

        @client()
        class Nothing:
            pass
