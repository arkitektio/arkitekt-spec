"""A blok's declared reads: queries and subscriptions on a service, typed by its schema.

When the app knows the service's GraphQL schema the build checks each document against it,
types ``queries.<name>.value`` by the query's selection, and refuses a mutation.
"""

import warnings
from pathlib import Path
from typing import Annotated

import pytest

from arkitekt_spec.actions import BlokQueryInput, BlokSubscriptionInput
from arkitekt_spec.blok.declared import declared_queries, declared_subscriptions
from arkitekt_spec.blok.vocabulary import BlokError
from arkitekt_spec.declare.app import AppRegistry
from arkitekt_spec.declare.blok import bsx
from arkitekt_spec.declare.blok.graphql import check_query, read_only_problem, schema_hash
from arkitekt_spec.declare.catalogs import CatalogWarning
from arkitekt_spec.declare.wiring import Alias, Require

pytest.importorskip("graphql")

SDL = """
type Dataset { id: ID! name: String! tags: [String!]! size: Int }
type Event { kind: String! at: String! }
type Query {
  datasets(folder: ID): [Dataset!]!
  dataset(id: ID!): Dataset
}
type Mutation { deleteDataset(id: ID!): Boolean }
type Subscription { events(dataset: ID!): Event! }
"""

READ = "query { datasets { id name } }"


class Client:
    pass


def _registry(tmp_path: Path, schema: bool = True) -> AppRegistry:
    registry = AppRegistry()
    sdl = tmp_path / "schema.graphql"
    sdl.write_text(SDL)

    @registry.service(schema=str(sdl) if schema else None, image="example/mikro:8")
    def mikro(mikro: Annotated[Alias, Require("live.arkitekt.mikro")]) -> Client:
        """Images."""
        return Client()

    return registry


LIST = '<foreach items={queries.cells.value.datasets} let="ds" key={ds.id}><p>{ds.name}</p></foreach>'


def test_a_query_answers_with_its_selection() -> None:
    shape = check_query(SDL, "query Q($folder: ID) { datasets(folder: $folder) { id label: name size } }")
    assert shape.errors == ()
    assert shape.data is not None
    (datasets,) = shape.data.children or ()
    (item,) = datasets.children or ()
    assert [(child.key, child.kind, child.nullable) for child in item.children or ()] == [
        ("id", "STRING", False),
        ("label", "STRING", False),
        ("size", "INT", True),
    ]
    assert [(v.name, v.type, v.required) for v in shape.variables] == [("folder", "ID", False)]


@pytest.mark.parametrize(
    ("document", "kind", "message"),
    [
        ("query { datasets { id nme } }", "query", "Cannot query field 'nme'"),
        ("mutation { deleteDataset(id: 1) }", "query", "never runs a mutation"),
        ("subscription { events(dataset: 1) { kind } }", "query", "holds a query operation"),
        ("query { a } query { b }", "query", "exactly one query"),
        ("query { datasets { id } }", "subscription", "holds a subscription operation"),
    ],
)
def test_a_document_that_is_not_its_kind_is_refused(document: str, kind: str, message: str) -> None:
    assert any(message in error for error in check_query(SDL, document, kind).errors)
    assert read_only_problem("mutation { x }", kind) is not None


def blok(*declarations: str, content: str = "<div />") -> str:
    """A panel blok declaring ``declarations`` before its content."""
    return f'<Blok kind="agent" context="panel">{"".join(declarations)}{content}</Blok>'


def query(name: str, document: str, *variables: str, element: str = "query", **attributes: object) -> str:
    """A ``<query>`` (or ``<subscription>``) with its ``<var>`` children."""
    said = "".join(f" {key}={{{value}}}" if isinstance(value, int) else f' {key}="{value}"' for key, value in attributes.items())
    head = f'<{element} name="{name}" service="mikro" document="{document}"{said}'
    return f"{head}>{''.join(variables)}</{element}>" if variables else f"{head} />"


def declared(registry: AppRegistry) -> tuple[tuple[BlokQueryInput, ...], tuple[BlokSubscriptionInput, ...]]:
    """What the one built blok declares, read back from the source it is sent as."""
    (built,) = registry.get_declared_bloks().values()
    assert built.source is not None
    tree = bsx(built.source)
    return declared_queries(tree), declared_subscriptions(tree)


def test_a_checked_query_types_its_value(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.register_blok("cells", blok(query("cells", READ), content=f"<div>{LIST}</div>"), mode="strict")

    (cells,), _ = declared(registry)
    assert (cells.key, cells.service, cells.document) == ("cells", "mikro", READ)


def test_a_read_of_what_the_query_does_not_select_is_refused(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    content = '<div><foreach items={queries.cells.value.datasets} let="ds" key={ds.id}><p>{ds.size}</p></foreach></div>'
    registry.register_blok("cells", blok(query("cells", READ), content=content))
    with pytest.raises(BlokError, match="'size' does not exist"):
        registry.get_declared_bloks()


def test_variables_are_expressions_and_a_required_one_is_set(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    document = "query($id: ID!) { dataset(id: $id) { name } }"
    source = blok(query("ds", document, '<var key="id" value={args.dataset} />'), content="<Text>{queries.ds.value.dataset.name}</Text>")
    registry.register_blok("one", source, args={"dataset": str}, mode="strict")
    (ds,), _ = declared(registry)
    assert ds.variables is not None and ds.variables[0].value_path == "args.dataset"

    missing = _registry(tmp_path)
    missing.register_blok("one", blok(query("ds", document)))
    with pytest.raises(BlokError, match="'id' is required"):
        missing.get_declared_bloks()


def test_a_field_the_schema_lacks_warns_or_refuses(tmp_path: Path) -> None:
    loose = _registry(tmp_path)
    loose.register_blok("loose", blok(query("q", "query { datasets { id nme } }")))
    with pytest.warns(CatalogWarning, match="Cannot query field 'nme'"):
        loose.get_declared_bloks()

    strict = _registry(tmp_path)
    strict.register_blok("strict", blok(query("q", "query { datasets { id nme } }")), mode="strict")
    with pytest.raises(BlokError, match="Cannot query field 'nme'"):
        strict.get_declared_bloks()


def test_a_service_without_a_known_schema_is_not_checked(tmp_path: Path) -> None:
    registry = _registry(tmp_path, schema=False)
    registry.register_blok("cells", blok(query("cells", READ), content="<p>{queries.cells.value.anything}</p>"))
    with pytest.warns(CatalogWarning, match="knows no schema"):
        registry.get_declared_bloks()


def test_a_subscription_streams_and_replays_from_a_query(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    events = query(
        "events",
        "subscription($d: ID!) { events(dataset: $d) { kind } }",
        '<var key="d" value={args.dataset} />',
        element="subscription",
        keep=20,
        history="cells",
    )
    registry.register_blok("events", blok(query("cells", READ), events, content="<Text>{subscriptions.events.value.kind}</Text>"), args={"dataset": str})
    with warnings.catch_warnings():
        warnings.simplefilter("error", CatalogWarning)
        _, (subscription,) = declared(registry)
    assert (subscription.keep, subscription.history) == (20, "cells")


def test_a_paged_query_fetches_more_into_its_list(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    document = "query($folder: ID) { datasets(folder: $folder) { id name } }"
    content = f"<div>{LIST}<Button onClick={{queries.cells.fetch_more(folder: args.folder)}}>More</Button><Spinner when={{queries.cells.fetching_more}} /></div>"
    registry.register_blok("cells", blok(query("cells", document, more="datasets"), content=content), args={"folder": str}, mode="strict")
    (cells,), _ = declared(registry)
    assert cells.more == "datasets"
    with pytest.raises(ValueError, match="at"):
        type(cells).model_validate({**cells.model_dump(), "at": "at"})


ONE = "query($id: ID!) { dataset(id: $id) { name tags } }"
ID = '<var key="id" value={"1"} />'


@pytest.mark.parametrize(
    ("document", "more", "problem"),
    [
        (READ, "dataset", "is no field of its result"),
        (ONE, "dataset", "is no list field"),
        (ONE, "dataset.name", "is no list field"),
        (READ, "datasets.0", "not a field path"),
    ],
)
def test_more_names_a_list_field_of_the_result(tmp_path: Path, document: str, more: str, problem: str) -> None:
    registry = _registry(tmp_path)
    variables = (ID,) if document == ONE else ()
    with pytest.raises(ValueError, match=rf"{problem}.*\[query.more\]"):
        registry.register_blok("cells", blok(query("cells", document, *variables, more=more)))
        registry.get_declared_bloks()


def test_more_may_name_a_nested_list(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.register_blok("cells", blok(query("cells", ONE, ID, more="dataset.tags")), mode="strict")
    (cells,), _ = declared(registry)
    assert cells.more == "dataset.tags"


def test_then_is_passed_to_a_query_as_an_ordinary_variable(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    document = "query($folder: ID) { datasets(folder: $folder) { id name } }"
    registry.register_blok("cells", blok(query("cells", document, '<var key="folder" value={then} />')), mode="strict")
    (cells,), _ = declared(registry)
    assert cells.variables is not None and cells.variables[0].value_path == "then"


def test_a_schema_hash_ignores_formatting() -> None:
    assert schema_hash(SDL) == schema_hash("\n\n" + SDL.replace("  ", "    "))


def test_a_federation_subgraph_schema_is_read_as_served() -> None:
    subgraph = 'type Dataset @key(fields: "id") { id: ID! name: String! }\ntype Query { datasets: [Dataset!]! }'
    assert check_query(subgraph, READ).errors == ()


VECTOR_SDL = """enum Color { RED GREEN }
interface Node { id: ID! }
type Dataset implements Node @key(fields: "id") { id: ID! name(upper: Boolean): String! tags: [String!]! color: Color }
union Hit = Dataset
input DatasetFilter { name: String ids: [ID!] }
type Query { datasets(filters: DatasetFilter): [Dataset!]! hit: Hit }
"""


def test_the_schema_hash_is_the_clients() -> None:
    """The vector orkestrator's ``core/modules/hooks/serviceSchema.test.ts`` checks too."""
    assert schema_hash(VECTOR_SDL) == "sha256:f92de02a4f48224cb38f968118e5f49c295b0faaaf71f6698ed66efd5a823504"
