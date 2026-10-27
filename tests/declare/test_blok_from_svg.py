"""``from_svg`` turns an SVG file into the BSX of a logo blok that registers."""

import re
import warnings
from collections.abc import Iterator
from typing import Any

import pytest

from arkitekt_spec.actions import ComponentNodeInput
from arkitekt_spec.declare.app import AppRegistry
from arkitekt_spec.declare.blok import SvgConversionError, SvgConversionWarning, bsx, from_svg
from arkitekt_spec.declare.blok.validate import BlokScope, check_blok_tree

LUCIDE = """<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none"
    stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="lucide">
  <path d="M6 2v14a2 2 0 0 0 2 2h14"/>
  <path d="M18 22V8a2 2 0 0 0-2-2H2"/>
</svg>"""

INKSCAPE = """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"
     xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
     xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd"
     width="64mm" height="64mm" viewBox="0 0 64 64" version="1.1" id="svg1" inkscape:version="1.3">
  <title>Cell</title>
  <sodipodi:namedview id="namedview1" pagecolor="#ffffff" />
  <metadata><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" /></metadata>
  <defs id="defs1">
    <style>
      /* classes, ids and tags */
      .cell { fill: #34d399; stroke: #0e7490 }
      #nucleus { stroke-width: 3px }
      rect.frame { stroke: #94a3b8 !important }
    </style>
    <linearGradient id="base"><stop offset="0" style="stop-color:#0b1220" /><stop offset="100%" stop-color="#1b2a44" /></linearGradient>
    <linearGradient id="ground" xlink:href="#base" x1="0" y1="0" x2="1" y2="1" />
    <linearGradient id="unused"><stop offset="0" stop-color="red" /></linearGradient>
    <clipPath id="tile"><rect width="64" height="64" rx="14" /></clipPath>
  </defs>
  <g inkscape:label="Layer 1" inkscape:groupmode="layer" id="layer1">
    <g clip-path="url(#tile)">
      <rect width="64" height="64" fill="url(#ground)" />
      <circle id="nucleus" class="cell" cx="32" cy="32" r="12"><title>nucleus</title></circle>
      <rect class="frame" x="0.5" y="0.5" width="63" height="63" style="fill:none;stroke-opacity:30%" />
    </g>
  </g>
</svg>"""


def svg(body: str, attributes: str = 'viewBox="0 0 24 24"') -> str:
    return f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" {attributes}>{body}</svg>'


def convert(source: str, **kwargs: Any) -> tuple[str, list[str]]:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        bsx_source = from_svg(source, **kwargs)
    return bsx_source, [
        str(w.message) for w in caught if issubclass(w.category, SvgConversionWarning)
    ]


def nodes(tree: ComponentNodeInput) -> Iterator[ComponentNodeInput]:
    yield tree
    for child in tree.children or ():
        yield from nodes(child)


def props(node: ComponentNodeInput) -> dict[str, object]:
    return {prop.key: prop.static_value for prop in node.props or ()}


def drawn(source: str, component: str) -> list[dict[str, object]]:
    return [props(node) for node in nodes(bsx(source)) if node.component == component]


def register(source: str) -> None:
    """Register a converted logo; its root (``<AgentLogo>``/``<ActionLogo>``) names its context."""
    AppRegistry().register_blok("converted", source)


# --------------------------------------------------------------------------- reading


def test_an_icon_set_svg_keeps_its_root_paint_on_a_group() -> None:
    source, lost = convert(LUCIDE)
    assert lost == []
    root = bsx(source)
    assert root.component == "AgentLogo"
    assert props(root) == {"viewBox": "0 0 24 24"}
    (group,) = root.children or ()
    assert props(group) == {
        "fill": "none",
        "stroke": "ink",
        "strokeWidth": "2",
        "strokeLinecap": "round",
        "strokeLinejoin": "round",
    }
    assert [props(path)["d"] for path in group.children or ()] == [
        "M6 2v14a2 2 0 0 0 2 2h14",
        "M18 22V8a2 2 0 0 0-2-2H2",
    ]
    register(source)


def test_an_inkscape_file_is_flattened_through_its_cascade() -> None:
    source, lost = convert(INKSCAPE)
    assert lost == []
    root = bsx(source)
    assert props(root) == {"viewBox": "0 0 64 64", "title": "Cell"}

    (defs, drawing) = root.children or ()
    assert defs.component == "Defs"
    assert [(node.component, node.id) for node in defs.children or ()] == [
        ("ClipPath", "tile"),
        ("LinearGradient", "ground"),
    ]
    gradient = defs.children[1]
    assert props(gradient) == {"x1": "0", "y1": "0", "x2": "1", "y2": "1"}
    assert [props(stop) for stop in gradient.children or ()] == [
        {"offset": "0", "stopColor": "ink"},
        {"offset": "100%", "stopColor": "brand-strong"},
    ]

    # The layer group had no props: it gave its child to the root, which inks what nothing paints.
    assert drawing.component == "G" and props(drawing) == {"clipPath": "url(#tile)", "fill": "ink"}
    (ground, cell, frame) = drawing.children or ()
    assert props(ground)["fill"] == "url(#ground)"
    assert props(cell) == {
        "title": "nucleus",
        "cx": "32",
        "cy": "32",
        "r": "12",
        "fill": "brand",
        "stroke": "brand-strong",
        "strokeWidth": "3",
    }
    assert props(frame)["stroke"] == "brand"
    assert props(frame)["fill"] == "none"
    assert props(frame)["strokeOpacity"] == "0.3"
    assert "unused" not in source
    register(source)


def test_a_file_and_its_text_convert_alike(tmp_path) -> None:
    path = tmp_path / "crop.svg"
    path.write_text(LUCIDE)
    assert from_svg(path) == from_svg(LUCIDE) == from_svg(LUCIDE.encode())


def test_hex_colours_become_the_nearest_pen_and_no_ids_are_printed() -> None:
    source, _ = convert(
        svg('<rect id="r" width="4" height="4" fill="#fff" /><circle r="2" fill="#abcdef" />')
    )
    assert "id=" not in source
    assert [shape["fill"] for shape in drawn(source, "Rect") + drawn(source, "Circle")] == [
        "surface",
        "brand-soft",
    ]
    register(source)


def test_entities_and_other_roots_are_refused() -> None:
    with pytest.raises(SvgConversionError, match="entities"):
        from_svg('<!DOCTYPE svg [<!ENTITY a "aaaa">]><svg>&a;</svg>')
    with pytest.raises(SvgConversionError, match="root is <html>"):
        from_svg("<html />")
    with pytest.raises(SvgConversionError, match="not well-formed"):
        from_svg("<svg>")
    with pytest.raises(ValueError, match="logo bloks"):
        from_svg(LUCIDE, context="scene")


def test_a_missing_viewbox_is_made_from_the_size() -> None:
    source, _ = convert(svg('<path d="M0 0" />', 'width="48px" height="32"'))
    assert props(bsx(source)) == {"viewBox": "0 0 48 32"}


# --------------------------------------------------------------------------- references


def test_use_and_symbol_are_expanded_and_a_shared_gradient_is_defined_once() -> None:
    source, lost = convert(
        svg(
            '<defs><radialGradient id="glow" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="gold" /></radialGradient>'
            '<symbol id="dot" viewBox="0 0 10 10"><circle cx="5" cy="5" r="4" fill="url(#glow)" /></symbol></defs>'
            '<use href="#dot" x="2" y="2" width="20" height="20" />'
            '<use xlink:href="#dot" width="10" height="10" opacity=".5" />'
        )
    )
    assert lost == []
    assert [node.id for node in nodes(bsx(source)) if node.component == "RadialGradient"] == [
        "glow"
    ]
    assert drawn(source, "Circle") == [
        {
            "cx": "5",
            "cy": "5",
            "r": "4",
            "fill": "url(#glow)",
            "transform": "translate(2 2) scale(2)",
        },
        {"cx": "5", "cy": "5", "r": "4", "fill": "url(#glow)", "opacity": ".5"},
    ]
    register(source)


def test_a_use_of_itself_is_dropped() -> None:
    _, lost = convert(svg('<g id="loop"><use href="#loop" /><path d="M0 0" /></g>'))
    assert lost == ["<use href='#loop'> uses itself and was dropped"]


def test_a_gradient_inherits_through_its_href_chain() -> None:
    source, _ = convert(
        svg(
            '<linearGradient id="a" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="#000" /></linearGradient>'
            '<radialGradient id="b" xlink:href="#a" r="9" />'
            '<circle r="9" fill="url(#b) red" />'
        )
    )
    (gradient,) = [node for node in nodes(bsx(source)) if node.component == "RadialGradient"]
    assert props(gradient) == {"r": "9", "gradientUnits": "userSpaceOnUse"}
    assert [props(stop) for stop in gradient.children or ()] == [
        {"offset": "0", "stopColor": "ink"}
    ]


def test_a_paint_naming_something_else_falls_back() -> None:
    source, lost = convert(
        svg(
            '<defs><pattern id="p" /></defs><rect width="4" height="4" fill="url(#p) #123456" /><rect width="2" height="2" fill="url(#gone)" />'
        )
    )
    assert [rect["fill"] for rect in drawn(source, "Rect")] == ["brand-strong", "none"]
    assert lost == [
        "url(#p) names a <pattern>, which a logo cannot paint with; it was dropped",
        "url(#gone) names no element; the paint was dropped",
    ]


# --------------------------------------------------------------------------- loss


def test_what_a_logo_cannot_draw_is_dropped_with_a_warning() -> None:
    body = (
        '<mask id="m"><rect width="4" height="4" fill="white" /></mask>'
        '<filter id="f" /><image href="x.png" /><script>alert(1)</script>'
        '<path d="M0 0" mask="url(#m)" onclick="alert(1)" stroke-miterlimit="4" />'
    )
    source, lost = convert(svg(body))
    assert drawn(source, "Path") == [{"d": "M0 0", "fill": "ink"}]
    assert "alert" not in source and "script" not in source.lower()
    assert lost == [
        "<mask> has no logo component and was dropped",
        "<filter> has no logo component and was dropped",
        "<image> has no logo component and was dropped",
        "<script> has no logo component and was dropped",
        "'mask' on <path> has no logo prop and was dropped",
        "the event handler 'onclick' was dropped",
        "'stroke-miterlimit' on <path> has no logo prop and was dropped",
    ]

    with pytest.raises(SvgConversionError, match="<mask> has no logo component"):
        from_svg(svg(body), strict=True)


def test_text_that_would_read_as_an_expression_is_dropped() -> None:
    source, lost = convert(
        svg('<title>@home</title><text x="1" y="2">$5 <tspan>off</tspan></text><text>ok</text>')
    )
    assert "title" not in props(bsx(source))
    assert drawn(source, "SvgText") == [{"text": "ok", "fill": "ink"}]
    assert lost == [
        "title='@home' reads as an expression in BSX and was dropped",
        "text='$5 off' reads as an expression in BSX and was dropped",
    ]


def test_text_inherits_its_group_font() -> None:
    source, _ = convert(
        svg(
            '<g font-family="Inter" font-size="8px" fill="#000"><text x="1" y="2" text-anchor="middle">A<tspan>B</tspan></text></g>'
        )
    )
    assert drawn(source, "SvgText") == [
        {
            "x": "1",
            "y": "2",
            "textAnchor": "middle",
            "fontFamily": "Inter",
            "fontSize": "8",
            "text": "AB",
            "fill": "ink",
        }
    ]


# --------------------------------------------------------------------------- action logos


def test_an_action_logo_paints_with_tokens_only() -> None:
    source, _ = convert(
        svg(
            '<linearGradient id="g"><stop offset="50%" stop-color="#ffffff" /><stop offset="1" /></linearGradient>'
            '<rect width="4" height="4" fill="#000" stroke="#777" />'
            '<rect width="4" height="4" fill="#fff" stroke="#0e7490" />'
            '<rect width="4" height="4" fill="#34d399" stroke="#d9fbe9" />'
            '<rect width="4" height="4" fill="rgba(52, 211, 153, .5)" stroke="url(#g)" />'
            '<rect width="4" height="4" fill="#fbbf24" />'
        ),
        context="action_logo",
        palette={"#fbbf24": "brand-strong"},
    )
    tree = bsx(source)
    assert tree.component == "ActionLogo"
    assert check_blok_tree(tree, BlokScope(kind="action_logo", context="ACTION_LOGO")) == []
    assert [(rect.get("fill"), rect.get("stroke")) for rect in drawn(source, "Rect")] == [
        ("ink", "muted"),
        ("surface", "brand-strong"),
        ("brand", "brand-soft"),
        ("brand", "url(#g)"),
        ("brand-strong", None),
    ]
    assert drawn(source, "Rect")[3]["fillOpacity"] == "0.5"
    assert drawn(source, "Stop") == [
        {"offset": "50%", "stopColor": "surface"},
        {"offset": "1", "stopColor": "ink"},
    ]
    register(source)


def test_an_unpainted_shape_in_an_action_logo_is_inked() -> None:
    source, _ = convert(svg('<path d="M0 0L4 4" />'), context="action_logo")
    (path,) = [node for node in nodes(bsx(source)) if node.component == "Path"]
    assert props(path) == {"d": "M0 0L4 4", "fill": "ink"}
    register(source)


def test_an_action_logo_registers_inline_on_an_action() -> None:
    def act(x: int) -> int:
        return x

    registry = AppRegistry()
    registry.register_action(act, key="act", logo=bsx(from_svg(LUCIDE, context="action_logo")))
    agent = registry.to_implement_agent_input()
    assert [blok.key for blok in agent.bloks or ()] == ["act.logo"]


@pytest.mark.parametrize(
    ("context", "expected"),
    [("logo", ["brand", "brand", "ink"]), ("action_logo", ["brand", "brand", "ink"])],
)
def test_current_colour_paints_the_colour_set_around_it(context: str, expected: list[str]) -> None:
    source, lost = convert(
        svg(
            '<path d="M0 0" fill="currentColor" />'
            '<g color="#2563eb"><path d="M1 1" stroke="currentColor" fill="none" /></g>'
            '<g color="currentColor"><path d="M2 2" fill="currentColor" /></g>',
            'viewBox="0 0 24 24" color="#e11d48"',
        ),
        context=context,
    )
    assert lost == []
    assert [
        path.get("fill") if path.get("fill") != "none" else path["stroke"]
        for path in drawn(source, "Path")
    ] == [
        *expected[:2],
        expected[0],
    ]
    without, _ = convert(svg('<path d="M0 0" fill="currentColor" />'), context=context)
    assert drawn(without, "Path")[0]["fill"] == expected[2]


def test_a_palette_maps_colours_of_a_logo_too() -> None:
    source, _ = convert(
        LUCIDE.replace('stroke="currentColor"', 'stroke="black"'), palette={"#000": "ink"}
    )
    assert drawn(source, "G")[0]["stroke"] == "ink"
    with pytest.raises(ValueError, match="not one of"):
        from_svg(LUCIDE, palette={"#000": "black"})


# --------------------------------------------------------------------------- limits


def test_groups_collapse_to_keep_a_deep_file_within_the_depth_limit() -> None:
    deep = svg(
        "<g>" * 10
        + '<g transform="translate(1 1)"><g opacity=".5"><path d="M0 0" /></g></g>'
        + "</g>" * 10
    )
    source, _ = convert(deep)
    assert drawn(source, "Path") == [{"d": "M0 0", "opacity": ".5", "transform": "translate(1 1)", "fill": "ink"}]
    register(source)


def test_precision_rounds_path_data_into_the_budget() -> None:
    d = "M" + " ".join(f"{i}.123456789 {i}.987654321" for i in range(800)) + "a1 1 0 011.5 1z"
    with pytest.raises(SvgConversionError, match=re.escape("pass precision=2")):
        from_svg(svg(f'<path d="{d}" />'))

    source = from_svg(svg(f'<path d="{d}" transform="rotate(12.3456 1 1)" />'), precision=1)
    (path,) = drawn(source, "Path")
    assert str(path["d"]).startswith("M.1 1 1.1 2 2.1 3")
    assert str(path["d"]).endswith("a1 1 0 0 1 1.5 1z")
    assert path["transform"] == "rotate(12.3 1 1)"
    register(source)
