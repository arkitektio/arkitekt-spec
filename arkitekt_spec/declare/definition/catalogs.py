"""Validate a definition's port calls and widgets against a catalog.

The blok side has a component walker (:mod:`rekuest.blok.walk`); a definition does not,
so this module is the one place that knows where a definition hides calls: validators and
effects on args, returns and their nested children, port-group effects, optimistic pointer
calls, and every widget, including fallback chains and the ports a SEARCH widget filters on.

It mirrors the server's ``facade.catalog_validation`` -- ``iter_definition_calls``,
``iter_definition_widgets``, ``iter_widget_calls`` and the CUSTOM-widget-as-one-node rule.
"""

from collections.abc import Iterator, Sequence
from typing import Any

from arkitekt_spec.actions import (
    AssignWidgetInput,
    DefinitionInput,
    OptimisticInput,
    ReturnWidgetInput,
    UtilCallInput,
)
from arkitekt_spec.declare.catalogs import CatalogView, Diagnostic, check_call, check_component
from arkitekt_spec.rules import iter_call_arguments


def _prop_calls(prop: Any) -> Iterator[UtilCallInput]:
    """Every util call a component prop carries.

    Mirrors the server's ``_prop_calls``: the prop's own ``util_call``, plus every util
    call nested in an ``agent_call``'s argument tree. Missing the second kind would let
    a prop bound to an agent call smuggle an unchecked operation past the client.
    """
    util_call = getattr(prop, "util_call", None)
    if util_call is not None:
        yield util_call
    agent_call = getattr(prop, "agent_call", None)
    if agent_call is not None:
        for argument in agent_call.arguments or ():
            if argument.util_call is not None:
                yield argument.util_call
            for nested in iter_call_arguments(argument.util_call) if argument.util_call else ():
                if nested.util_call is not None:
                    yield nested.util_call

Widget = AssignWidgetInput | ReturnWidgetInput

CUSTOM_WIDGET_KIND = "CUSTOM"


def iter_widget_calls(widget: Widget) -> Iterator[UtilCallInput]:
    """Every call a widget carries besides its CUSTOM props: state pointers and accessors."""
    state_call = getattr(widget, "state_call", None)
    if state_call is not None:
        yield state_call
    for accessor in getattr(widget, "state_accessors", None) or ():
        call = getattr(accessor, "call", None)
        if call is not None:
            yield call


def iter_definition_widgets(definition: DefinitionInput) -> Iterator[tuple[str, Widget]]:
    """Every widget of a definition, with a label naming where it lives.

    Walks args and returns, their nested children, each widget's fallback chain, and the
    filter ports a SEARCH widget carries.
    """

    def widgets_of(widget: Widget | None, owner: str) -> Iterator[tuple[str, Widget]]:
        depth = 0
        while widget is not None:
            yield (owner if depth == 0 else f"{owner} fallback {depth}", widget)
            # Each widget in the chain owns its own filter ports; label them by depth so
            # a fallback's filters are not confused with -- or counted twice as -- the
            # head widget's.
            filter_owner = owner if depth == 0 else f"{owner} fallback {depth}"
            yield from walk(
                getattr(widget, "filters", None) or (), f"{filter_owner} filter"
            )
            widget = getattr(widget, "fallback", None)
            depth += 1

    def walk(ports: Sequence[Any], prefix: str) -> Iterator[tuple[str, Widget]]:
        for port in ports:
            yield from widgets_of(getattr(port, "widget", None), f"{prefix} port {port.key}")
            yield from walk(port.children or (), prefix)

    yield from walk(definition.args or (), f"Definition {definition.key}")
    yield from walk(definition.returns or (), f"Definition {definition.key}")


def iter_definition_calls(
    definition: DefinitionInput,
    optimistics: Sequence[OptimisticInput] | None = None,
) -> Iterator[tuple[str, UtilCallInput]]:
    """Every validator and effect call of a definition, plus optimistic pointer calls."""

    def walk(ports: Sequence[Any], prefix: str) -> Iterator[tuple[str, UtilCallInput]]:
        for port in ports:
            owner = f"{prefix} port {port.key}"
            for validator in getattr(port, "validators", None) or ():
                yield f"validator of {owner}", validator.call
            for effect in port.effects or ():
                yield f"effect of {owner}", effect.call
            yield from walk(port.children or (), prefix)

    label = f"Definition {definition.key}"
    yield from walk(definition.args or (), label)
    yield from walk(definition.returns or (), label)

    for group in definition.port_groups or ():
        for effect in group.effects or ():
            yield f"effect of {label} port group {group.key}", effect.call

    for optimistic in optimistics or ():
        if optimistic.path_call is not None:
            yield f"optimistic of {label}", optimistic.path_call


def _check_call_tree(
    call: UtilCallInput, view: CatalogView, owner: str
) -> list[Diagnostic]:
    """Check ``call`` and every util call nested in its argument tree."""
    diagnostics: list[Diagnostic] = []
    candidates = [call]
    for argument in iter_call_arguments(call):
        if argument.util_call is not None:
            candidates.append(argument.util_call)
    for candidate in candidates:
        finding = check_call(
            candidate.operation,
            [a.key for a in candidate.arguments or () if a.key is not None],
            view,
            owner,
        )
        if finding is not None:
            diagnostics.append(finding)
    return diagnostics


def validate_definition_against_catalog(
    definition: DefinitionInput,
    view: CatalogView,
    optimistics: Sequence[OptimisticInput] | None = None,
) -> list[Diagnostic]:
    """Check every call and widget of ``definition`` against ``view``.

    A CUSTOM widget is validated as a one-node component manifest, exactly as on the
    server, so an unknown widget component is caught by the same rule as an unknown
    blok component.

    Returns:
        The non-fatal findings, e.g. operations the catalog does not provide.

    Raises:
        ValueError: If a known operation is misused, or a component rule is broken.
    """
    diagnostics: list[Diagnostic] = []

    for owner, call in iter_definition_calls(definition, optimistics):
        diagnostics.extend(_check_call_tree(call, view, owner))

    for owner, widget in iter_definition_widgets(definition):
        widget_owner = f"widget of {owner}"
        if getattr(widget, "kind", None) == CUSTOM_WIDGET_KIND:
            props = getattr(widget, "props", None) or ()
            check_component(
                widget.component,
                [prop.key for prop in props],
                False,
                {
                    prop.key: getattr(prop, "agent_call", None) is not None
                    or getattr(prop, "util_call", None) is not None
                    for prop in props
                },
                view,
                widget_owner,
            )
        for prop in getattr(widget, "props", None) or ():
            prop_owner = f"prop {prop.key!r} of {widget_owner}"
            for call in _prop_calls(prop):
                diagnostics.extend(_check_call_tree(call, view, prop_owner))
        for call in iter_widget_calls(widget):
            diagnostics.extend(_check_call_tree(call, view, widget_owner))

    return diagnostics
