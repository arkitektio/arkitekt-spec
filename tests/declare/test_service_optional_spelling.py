"""An optional requirement may be spelled either way."""

from typing import Annotated, Optional

from arkitekt_spec.declare.service import _wants_alias
from arkitekt_spec.declare.wiring import Alias


def test_optional_and_pipe_none_both_read_as_an_optional_alias():
    assert _wants_alias(Optional[Alias]) == (True, True)  # noqa: UP045
    assert _wants_alias(Alias | None) == (True, True)
    assert _wants_alias(Alias) == (True, False)
    assert _wants_alias(Annotated[int, "x"]) == (False, False)
