"""A blok's own logo: the ``<logo>`` declaration."""

import pytest

from arkitekt_spec.blok.bsx import parse_bsx
from arkitekt_spec.blok.declared import (
    LOGO_VIEW_BOX,
    blok_logo_tree,
    content,
    declared_logo,
)
from arkitekt_spec.blok.vocabulary import BlokError
from arkitekt_spec.declare.blok.validate import validate_bsx

LOGO = (
    '<logo title="Scope"><Circle cx="12" cy="12" r="7" /><Path d="M12 5v14" /></logo>'
)


def panel(inner: str, kind: str = "agent") -> str:
    return f'<Blok kind="{kind}" context="panel">{inner}<Card><Text text="hi" /></Card></Blok>'


def codes(source: str) -> list[str]:
    return [finding.code for finding in validate_bsx(source)]


@pytest.mark.parametrize("kind", ["agent", "custom"])
def test_a_panel_of_any_kind_may_say_what_it_looks_like(kind: str) -> None:
    assert codes(panel(LOGO, kind)) == []


def test_the_logo_draws_nothing_in_the_blok() -> None:
    tree = parse_bsx(panel(LOGO))
    assert [node.component for node in content(tree)] == ["Card"]
    logo = declared_logo(tree)
    assert logo is not None and logo.component == "logo"


def test_it_is_drawn_as_a_logo_blok_of_its_own() -> None:
    tree = parse_bsx(panel(LOGO))
    logo = blok_logo_tree(tree)
    assert logo is not None
    props = {prop.key: prop.static_value for prop in logo.props or ()}
    assert props == {"kind": "agent", "context": "logo"}
    assert logo.children is not None
    drawing = logo.children[0]
    assert drawing.component == "Logo"
    drawn = {prop.key: prop.static_value for prop in drawing.props or ()}
    assert drawn == {"viewBox": LOGO_VIEW_BOX, "title": "Scope"}
    assert drawing.children is not None
    pen = drawing.children[0]
    assert pen.component == "G"
    assert {prop.key: prop.static_value for prop in pen.props or ()}["stroke"] == "ink"
    assert [child.component for child in pen.children or ()] == ["Circle", "Path"]
    assert blok_logo_tree(parse_bsx(panel(""))) is None


def test_it_paints_with_the_logo_pens() -> None:
    with pytest.raises(BlokError, match="palette.logo_paint"):
        validate_bsx(panel('<logo><Circle r="4" fill="#ff0000" /></logo>'))


def test_it_is_a_child_of_the_blok_and_there_is_one() -> None:
    with pytest.raises(BlokError, match="logo.misplaced"):
        validate_bsx(
            '<Blok kind="agent" context="panel"><Card><logo><Circle r="4" /></logo></Card></Blok>'
        )
    with pytest.raises(BlokError, match="logo.twice"):
        validate_bsx(panel(LOGO + LOGO))


def test_a_logo_blok_declares_none() -> None:
    with pytest.raises(BlokError, match="logo.in_logo"):
        validate_bsx(
            f'<Blok kind="agent" context="logo">{LOGO}<Logo><Circle r="4" /></Logo></Blok>'
        )


def test_it_is_a_fixed_drawing() -> None:
    found = codes(panel("<logo><Circle r={self.Stage.radius} /></logo>"))
    assert "logo.dynamic" in found
