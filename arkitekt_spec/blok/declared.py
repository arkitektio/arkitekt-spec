"""What a blok says of itself in its tree: its ``<Blok>`` header and its declarations.

Every blok is one ``<Blok kind=... context=...>`` element (``vocabulary.json`` ``blok``):

- the header (:func:`blok_header`): its kind, its context, the catalog it is written for and the
  host's chrome, all literals on the root;
- its declarations, each a node of its own that draws nothing: ``<local>`` anywhere (first among
  its parent's children, in scope in its parent's subtree), ``<query>`` and ``<subscription>``
  as children of the ``<Blok>``, each with ``<var>`` children for its variables.

Nothing here checks a rule beyond what makes a declaration readable at all; the validator
(:mod:`arkitekt_spec.declare.blok.validate`) says the rest.
"""

from collections.abc import Iterator
from dataclasses import dataclass

from arkitekt_spec.actions import (
    ActionArgumentInput,
    BlokLocalInput,
    BlokQueryInput,
    BlokSubscriptionInput,
    ComponentContext,
    ComponentNodeInput,
    ComponentPropInput,
)
from arkitekt_spec.blok.vocabulary import BlokError, Finding, load_vocabulary

BLOK_COMPONENT = "Blok"
LOCAL_COMPONENT = "local"
QUERY_COMPONENT = "query"
SUBSCRIPTION_COMPONENT = "subscription"
VAR_COMPONENT = "var"
DECLARATION_COMPONENTS = frozenset(
    {LOCAL_COMPONENT, QUERY_COMPONENT, SUBSCRIPTION_COMPONENT, VAR_COMPONENT}
)
"""The components that declare what a blok owns and draw nothing."""
NAME_PROP = "name"
"""The name a local, a query or a subscription is read by."""
LIKE_PROP = "like"
"""A local's borrowed port: ``self.<action>.<arg>`` or ``deps.<key>.<action>.<arg>``."""
KEY_PROP = "key"
VALUE_PROP = "value"
KIND_ATTRIBUTE = "kind"
CONTEXT_ATTRIBUTE = "context"
CATALOG_ATTRIBUTE = "catalog"
CHROME_ATTRIBUTE = "chrome"
_LOCAL_FIELDS = frozenset(BlokLocalInput.model_fields) - {"key"}
"""What a ``<local>`` may say beside its name: a port's fields and ``persist``."""


@dataclass(frozen=True)
class BlokHeader:
    """What a ``<Blok>`` says of itself."""

    kind: str
    """``agent``, ``custom``, ``task``, ``action`` or ``assign``."""
    context: str
    """``PANEL``, ``SCENE`` or ``LOGO`` (a :class:`ComponentContext` value)."""
    catalog: str | None
    chrome: str | None
    node: str
    """The root's id."""


def _static(node: ComponentNodeInput, key: str) -> ComponentPropInput | None:
    return next((prop for prop in node.props or () if prop.key == key), None)


def _is_literal(prop: ComponentPropInput) -> bool:
    return prop.dynamic_value is None and prop.util_call is None and prop.agent_call is None


def header_problem(tree: ComponentNodeInput) -> Finding | None:
    """Why ``tree`` is no readable ``<Blok>``, or None: ``blok.root`` or ``blok.attribute``."""
    vocabulary = load_vocabulary()
    if tree.component != vocabulary.blok.component:
        return Finding.of("blok.root", tree.id, component=tree.component)
    kinds = "|".join(vocabulary.kinds)
    for key in (KIND_ATTRIBUTE, CONTEXT_ATTRIBUTE):
        prop = _static(tree, key)
        if prop is None:
            return Finding.of("blok.attribute", tree.id, problem=f'needs {key}="..."', kinds=kinds)
        if not _is_literal(prop) or not isinstance(prop.static_value, str):
            return Finding.of(
                "blok.attribute", tree.id, problem=f"takes {key} as a literal string", kinds=kinds
            )
    kind = _static(tree, KIND_ATTRIBUTE)
    assert kind is not None
    if kind.static_value not in vocabulary.kinds:
        return Finding.of(
            "blok.attribute", tree.id, problem=f"has no kind {kind.static_value!r}", kinds=kinds
        )
    context = _static(tree, CONTEXT_ATTRIBUTE)
    assert context is not None
    if context.static_value not in vocabulary.blok.contexts:
        return Finding.of(
            "blok.attribute",
            tree.id,
            problem=f"has no context {context.static_value!r} ({', '.join(vocabulary.blok.contexts)})",
            kinds=kinds,
        )
    for key in (CATALOG_ATTRIBUTE, CHROME_ATTRIBUTE):
        prop = _static(tree, key)
        if prop is not None and (not _is_literal(prop) or not isinstance(prop.static_value, str)):
            return Finding.of(
                "blok.attribute", tree.id, problem=f"takes {key} as a literal string", kinds=kinds
            )
    return None


def blok_header(tree: ComponentNodeInput) -> BlokHeader:
    """The header of a blok: what its ``<Blok>`` says.

    Raises:
        BlokError: ``blok.root`` if the root is no ``<Blok>``, ``blok.attribute`` if its kind or
            context is missing, no literal or unknown.
    """
    problem = header_problem(tree)
    if problem is not None:
        raise BlokError.from_finding(problem)
    vocabulary = load_vocabulary()

    def text(key: str) -> str | None:
        prop = _static(tree, key)
        return (
            prop.static_value if prop is not None and isinstance(prop.static_value, str) else None
        )

    return BlokHeader(
        kind=text(KIND_ATTRIBUTE) or "",
        context=vocabulary.blok.contexts[text(CONTEXT_ATTRIBUTE) or ""],
        catalog=text(CATALOG_ATTRIBUTE),
        chrome=text(CHROME_ATTRIBUTE),
        node=tree.id,
    )


def blok_context(tree: ComponentNodeInput) -> ComponentContext:
    """The context a blok's ``<Blok>`` names.

    Raises:
        BlokError: As :func:`blok_header`.
    """
    return ComponentContext(blok_header(tree).context)


def blok_kind(tree: ComponentNodeInput) -> str:
    """The kind a blok's ``<Blok>`` names.

    Raises:
        BlokError: As :func:`blok_header`.
    """
    return blok_header(tree).kind


def blok_catalog(tree: ComponentNodeInput) -> str | None:
    """The catalog a blok's ``<Blok>`` names, None for the base catalog (and for no ``<Blok>``)."""
    prop = _static(tree, CATALOG_ATTRIBUTE) if tree.component == BLOK_COMPONENT else None
    return prop.static_value if prop is not None and isinstance(prop.static_value, str) else None


def is_declaration(node: ComponentNodeInput) -> bool:
    """Whether ``node`` declares something (a local, a query, ...) rather than drawing."""
    return node.component in DECLARATION_COMPONENTS


def content(node: ComponentNodeInput) -> tuple[ComponentNodeInput, ...]:
    """The children of ``node`` that draw: its declarations left out."""
    return tuple(child for child in node.children or () if not is_declaration(child))


def local_name(node: ComponentNodeInput) -> str | None:
    """The name a ``<local>`` declares, or None when it has none."""
    prop = _static(node, NAME_PROP)
    return prop.static_value if prop is not None and isinstance(prop.static_value, str) else None


def local_like(node: ComponentNodeInput) -> str | None:
    """The argument a ``<local>`` borrows its port from (``self.move.to``), or None."""
    prop = _static(node, LIKE_PROP)
    return prop.static_value if prop is not None and isinstance(prop.static_value, str) else None


def local_problem(node: ComponentNodeInput) -> Finding | None:
    """Why a ``<local>`` declares nothing readable, or None: ``local.attribute``."""
    name = local_name(node) or "?"
    if local_name(node) is None:
        return Finding.of(
            "local.attribute", node.id, name=name, key="name", problem='is missing: write name="x"'
        )
    for prop in node.props or ():
        if prop.key in (NAME_PROP, LIKE_PROP):
            continue
        if prop.key not in _LOCAL_FIELDS:
            return Finding.of(
                "local.attribute",
                node.id,
                name=name,
                key=prop.key,
                problem=f"is no field of a local ({', '.join(sorted(_LOCAL_FIELDS))}, like)",
            )
        if not _is_literal(prop):
            return Finding.of(
                "local.attribute", node.id, name=name, key=prop.key, problem="is not a literal"
            )
    if node.children:
        return Finding.of(
            "local.attribute",
            node.id,
            name=name,
            key="children",
            problem="are not elements: a local holds nothing",
        )
    try:
        declared_local(node)
    except BlokError as error:
        return Finding.of(
            "local.attribute",
            node.id,
            name=name,
            key="its port",
            problem=f"is no valid port ({error})",
        )
    return None


def declared_local(node: ComponentNodeInput) -> BlokLocalInput:
    """A ``<local>`` as the port it declares, its kind left out unless it gives one (``like`` is
    resolved where the blok is mounted).

    Raises:
        BlokError: ``local.attribute`` if it is no valid local.
    """
    from pydantic import ValidationError

    name = local_name(node)
    if name is None:
        raise BlokError(
            "local.attribute", name="?", key="name", problem='is missing: write name="x"'
        )
    fields: dict[str, object] = {"key": name}
    for prop in node.props or ():
        if prop.key in (NAME_PROP, LIKE_PROP) or prop.key not in _LOCAL_FIELDS:
            continue
        if prop.static_value is not None:
            fields[prop.key] = prop.static_value
    try:
        return BlokLocalInput.model_validate(fields)
    except ValidationError as error:
        raise BlokError(
            "local.attribute",
            name=name,
            key="its port",
            problem=f"is no valid port: {error.errors()[0]['msg']}",
        ) from None


def local_node(local: BlokLocalInput, node_id: str) -> ComponentNodeInput:
    """``local`` as the ``<local>`` node that declares it: every field it sets a literal prop."""
    fields = local.model_dump(mode="json", exclude_none=True, exclude={"key"})
    props = [ComponentPropInput(key=NAME_PROP, static_value=local.key, declares_value=local.key)]
    props.extend(
        ComponentPropInput(key=key, static_value=value)
        for key, value in fields.items()
        if value not in ((), [], False) or key == "default"
    )
    return ComponentNodeInput(id=node_id, component=LOCAL_COMPONENT, props=tuple(props))


def iter_with_parents(
    tree: ComponentNodeInput,
) -> Iterator[tuple[ComponentNodeInput, tuple[ComponentNodeInput, ...]]]:
    """Every node of ``tree`` with its ancestors, depth first."""

    def visit(
        node: ComponentNodeInput, ancestors: tuple[ComponentNodeInput, ...]
    ) -> Iterator[tuple[ComponentNodeInput, tuple[ComponentNodeInput, ...]]]:
        yield node, ancestors
        for child in node.children or ():
            yield from visit(child, (*ancestors, node))

    return visit(tree, ())


def declared_locals(tree: ComponentNodeInput) -> dict[str, BlokLocalInput]:
    """Every readable ``<local>`` of ``tree``, by its node id, in tree order."""
    found: dict[str, BlokLocalInput] = {}
    for node, _ in iter_with_parents(tree):
        if node.component == LOCAL_COMPONENT and local_problem(node) is None:
            found[node.id] = declared_local(node)
    return found


def var_argument(node: ComponentNodeInput) -> ActionArgumentInput | None:
    """A ``<var key value>`` as the argument it is stored as, or None when it is none."""
    key = _static(node, KEY_PROP)
    if key is None or not isinstance(key.static_value, str):
        return None
    value = _static(node, VALUE_PROP)
    if value is None or (
        value.static_value is None and value.dynamic_value is None and value.util_call is None
    ):
        return ActionArgumentInput(key=key.static_value)
    if value.agent_call is not None:
        return None
    if value.dynamic_value is not None and value.dynamic_value.path is not None:
        return ActionArgumentInput(key=key.static_value, value_path=value.dynamic_value.path)
    if value.util_call is not None:
        return ActionArgumentInput(key=key.static_value, util_call=value.util_call)
    return ActionArgumentInput(key=key.static_value, value_literal=value.static_value)


def declaration_problem(node: ComponentNodeInput) -> Finding | None:
    """Why a ``<query>`` or ``<subscription>`` declares nothing readable, or None: ``query.declaration``."""
    for key in (NAME_PROP, "service", "document"):
        prop = _static(node, key)
        if (
            prop is None
            or not _is_literal(prop)
            or not isinstance(prop.static_value, str)
            or not prop.static_value
        ):
            return Finding.of(
                "query.declaration",
                node.id,
                component=node.component,
                problem=f'needs {key}="..." as a literal string',
            )
    for prop in node.props or ():
        if prop.key not in (NAME_PROP, "service", "document") and not _is_literal(prop):
            return Finding.of(
                "query.declaration",
                node.id,
                component=node.component,
                problem=f"takes {prop.key} as a literal",
            )
    for child in node.children or ():
        if child.component != VAR_COMPONENT:
            return Finding.of(
                "query.declaration",
                child.id,
                component=node.component,
                problem=f"holds only <var> children, not <{child.component}>",
            )
        if var_argument(child) is None:
            return Finding.of(
                "query.declaration",
                child.id,
                component=node.component,
                problem='has a <var> without key="..." or with a task call as its value',
            )
    try:
        _declared(node)
    except BlokError as error:
        return Finding.of(
            "query.declaration", node.id, component=node.component, problem=str(error)
        )
    return None


def _declared(node: ComponentNodeInput) -> BlokQueryInput | BlokSubscriptionInput:
    from pydantic import ValidationError

    fields: dict[str, object] = {}
    for prop in node.props or ():
        if prop.static_value is not None:
            fields["key" if prop.key == NAME_PROP else prop.key] = prop.static_value
    fields["variables"] = tuple(
        argument for child in node.children or () if (argument := var_argument(child)) is not None
    )
    model = BlokQueryInput if node.component == QUERY_COMPONENT else BlokSubscriptionInput
    try:
        return model.model_validate(fields)
    except ValidationError as error:
        first = error.errors()[0]
        where = ".".join(str(part) for part in first["loc"])
        raise BlokError(
            "query.declaration", component=node.component, problem=f"{where}: {first['msg']}"
        ) from None


def declared_queries(tree: ComponentNodeInput) -> tuple[BlokQueryInput, ...]:
    """The readable ``<query>`` children of a blok's ``<Blok>``, in order."""
    return tuple(
        query
        for child in tree.children or ()
        if child.component == QUERY_COMPONENT and declaration_problem(child) is None
        for query in (_declared(child),)
        if isinstance(query, BlokQueryInput)
    )


def declared_subscriptions(tree: ComponentNodeInput) -> tuple[BlokSubscriptionInput, ...]:
    """The readable ``<subscription>`` children of a blok's ``<Blok>``, in order."""
    return tuple(
        subscription
        for child in tree.children or ()
        if child.component == SUBSCRIPTION_COMPONENT and declaration_problem(child) is None
        for subscription in (_declared(child),)
        if isinstance(subscription, BlokSubscriptionInput)
    )


__all__ = [
    "BLOK_COMPONENT",
    "DECLARATION_COMPONENTS",
    "LIKE_PROP",
    "LOCAL_COMPONENT",
    "NAME_PROP",
    "QUERY_COMPONENT",
    "SUBSCRIPTION_COMPONENT",
    "VAR_COMPONENT",
    "BlokHeader",
    "blok_catalog",
    "blok_context",
    "blok_header",
    "blok_kind",
    "content",
    "declaration_problem",
    "declared_local",
    "declared_locals",
    "declared_queries",
    "declared_subscriptions",
    "header_problem",
    "is_declaration",
    "iter_with_parents",
    "local_like",
    "local_name",
    "local_node",
    "local_problem",
    "var_argument",
]
