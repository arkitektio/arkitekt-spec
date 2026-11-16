"""A task's blok, as the runtime sends it: the fields of rekuest-protocol's ``TaskBlok``."""

import pytest
from pydantic import ValidationError

from arkitekt_spec.declare.task import TaskBlok

SOURCE = '<Blok kind="task" context="panel"><Card><Button>Go</Button></Card></Blok>'
EMPTY = '<Blok kind="task" context="panel"><Card /></Blok>'


def test_a_source_blok_says_its_language_and_the_catalog_it_is_written_for() -> None:
    catalog = "https://arkitekt.live/catalogs/orkestrator/v1"
    blok = TaskBlok(source=SOURCE, vocab=2, catalog=catalog)
    assert blok.model_dump(exclude_none=True) == {"source": SOURCE, "vocab": 2, "catalog": catalog}


@pytest.mark.parametrize("field", ["modules", "catalogs", "module", "now_interval"])
def test_the_names_before_revision_2_are_refused(field: str) -> None:
    with pytest.raises(ValidationError):
        TaskBlok.model_validate({"source": EMPTY, field: None})


def test_left_out_they_are_not_sent() -> None:
    assert TaskBlok(key="ask_image").model_dump(exclude_none=True) == {"key": "ask_image"}


def test_a_field_the_protocol_lacks_is_refused() -> None:
    with pytest.raises(ValidationError):
        TaskBlok.model_validate({"source": EMPTY, "persist": ["x"]})
