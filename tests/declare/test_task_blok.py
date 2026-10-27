"""A task's blok, as the runtime sends it: the fields of rekuest-protocol's ``TaskBlok``."""

import pytest
from pydantic import ValidationError

from arkitekt_spec.declare.task import TaskBlok


def test_a_source_blok_says_its_language_catalogs_and_module() -> None:
    blok = TaskBlok(source="<Html><Card><Button>Go</Button></Card></Html>", vocab=2, catalogs=["ui@1"], module="orkestrator")
    assert blok.model_dump(exclude_none=True) == {
        "source": "<Html><Card><Button>Go</Button></Card></Html>", "vocab": 2, "catalogs": ["ui@1"], "module": "orkestrator",
    }


@pytest.mark.parametrize("field", ["modules", "catalog", "now_interval"])
def test_the_names_before_revision_2_are_refused(field: str) -> None:
    with pytest.raises(ValidationError):
        TaskBlok.model_validate({"source": "<Html><Card /></Html>", field: None})


def test_a_source_blok_local_is_a_port() -> None:
    blok = TaskBlok(source="<Html><Card /></Html>", locals=[{"key": "n", "kind": "INT", "default": 3, "persist": True}])  # type: ignore[list-item]
    (local,) = blok.locals or ()
    assert (local.kind, local.default, local.persist) == ("INT", 3, True)
    (untyped,) = TaskBlok(source="<Html><Card /></Html>", locals=[{"key": "n", "default": 3}]).locals or ()  # type: ignore[list-item]
    assert untyped.kind == "ANY" and "kind" not in untyped.model_fields_set


def test_left_out_they_are_not_sent() -> None:
    assert TaskBlok(key="ask_image").model_dump(exclude_none=True) == {"key": "ask_image"}


def test_a_field_the_protocol_lacks_is_refused() -> None:
    with pytest.raises(ValidationError):
        TaskBlok.model_validate({"source": "<Html><Card /></Html>", "persist": ["x"]})
