"""A blok's declared reads: queries and subscriptions on a service, typed by its schema.

When the app knows the service's GraphQL schema the build checks each document against it,
types ``queries.<name>.value`` by the query's selection, and refuses a mutation.
"""

import warnings
from pathlib import Path
from typing import Annotated

import pytest

from arkitekt_spec.blok.vocabulary import BlokError
from arkitekt_spec.declare.app import AppRegistry
from arkitekt_spec.declare.blok.declarations import Query, Subscription
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


def test_a_checked_query_types_its_value(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.register_blok("cells", f"<Html><div>{LIST}</div></Html>", queries={"cells": Query(service="mikro", document=READ)}, mode="strict")
    (blok,) = registry.get_declared_bloks().values()

    (query,) = blok.queries
    assert (query.key, query.service, query.document) == ("cells", "mikro", READ)


def test_a_read_of_what_the_query_does_not_select_is_refused(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.register_blok(
        "cells",
        '<Html><div><foreach items={queries.cells.value.datasets} let="ds" key={ds.id}><p>{ds.size}</p></foreach></div></Html>',
        queries={"cells": Query(service="mikro", document=READ)},
    )
    with pytest.raises(BlokError, match="'size' does not exist"):
        registry.get_declared_bloks()


def test_variables_are_expressions_and_a_required_one_is_set(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    document = "query($id: ID!) { dataset(id: $id) { name } }"
    registry.register_blok(
        "one", "<Html><Text>{queries.ds.value.dataset.name}</Text></Html>", args={"dataset": str}, queries={"ds": Query(service="mikro", document=document, variables={"id": "args.dataset"})}, mode="strict"
    )
    (blok,) = registry.get_declared_bloks().values()
    assert blok.queries[0].variables[0].value_path == "args.dataset"

    missing = _registry(tmp_path)
    missing.register_blok("one", "<Html><Text /></Html>", queries={"ds": Query(service="mikro", document=document)})
    with pytest.raises(BlokError, match="'id' is required"):
        missing.get_declared_bloks()


def test_a_field_the_schema_lacks_warns_or_refuses(tmp_path: Path) -> None:
    loose = _registry(tmp_path)
    loose.register_blok("loose", "<Html><div /></Html>", queries={"q": Query(service="mikro", document="query { datasets { id nme } }")})
    with pytest.warns(CatalogWarning, match="Cannot query field 'nme'"):
        loose.get_declared_bloks()

    strict = _registry(tmp_path)
    strict.register_blok("strict", "<Html><div /></Html>", queries={"q": Query(service="mikro", document="query { datasets { id nme } }")}, mode="strict")
    with pytest.raises(BlokError, match="Cannot query field 'nme'"):
        strict.get_declared_bloks()


def test_a_mutation_is_refused_at_declaration() -> None:
    with pytest.raises(ValueError, match=r"is a mutation.*\[query.read_only\]"):
        AppRegistry().register_blok("m", "<Html><div /></Html>", queries={"q": Query(service="mikro", document="mutation { deleteDataset(id: 1) }")})
    with pytest.raises(ValueError, match=r"query 'q' is a subscription, not a query.*\[query.kind\]"):
        AppRegistry().register_blok("s", "<Html><div /></Html>", queries={"q": Query(service="mikro", document="subscription { datasets { id } }")})


def test_a_service_without_a_known_schema_is_not_checked(tmp_path: Path) -> None:
    registry = _registry(tmp_path, schema=False)
    registry.register_blok("cells", "<Html><p>{queries.cells.value.anything}</p></Html>", queries={"cells": Query(service="mikro", document=READ)})
    with pytest.warns(CatalogWarning, match="knows no schema"):
        registry.get_declared_bloks()


def test_a_subscription_streams_and_replays_from_a_query(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.register_blok(
        "events",
        "<Html><Text>{subscriptions.events.value.kind}</Text></Html>",
        args={"dataset": str},
        queries={"cells": Query(service="mikro", document=READ)},
        subscriptions={"events": Subscription(service="mikro", document="subscription($d: ID!) { events(dataset: $d) { kind } }", variables={"d": "args.dataset"}, keep=20, history="cells")},
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error", CatalogWarning)
        (blok,) = registry.get_declared_bloks().values()
    (subscription,) = blok.subscriptions
    assert (subscription.keep, subscription.history) == (20, "cells")

    with pytest.raises(ValueError, match="no declared query"):
        AppRegistry().register_blok("bad", "<Html><div /></Html>", subscriptions={"e": Subscription(service="mikro", document="subscription { events(dataset: 1) { kind } }", history="nope")})


def test_a_paged_query_fetches_more_into_its_list(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    document = "query($folder: ID) { datasets(folder: $folder) { id name } }"
    registry.register_blok(
        "cells",
        f"<Html><div>{LIST}<Button onClick={{queries.cells.fetch_more(folder: args.folder)}}>More</Button><Spinner when={{queries.cells.fetching_more}} /></div></Html>",
        args={"folder": str},
        queries={"cells": Query(service="mikro", document=document, more="datasets")},
        mode="strict",
    )
    (blok,) = registry.get_declared_bloks().values()
    (query,) = blok.queries
    assert query.more == "datasets"
    with pytest.raises(ValueError, match="at"):
        type(query).model_validate({**query.model_dump(), "at": "at"})


ONE = "query($id: ID!) { dataset(id: $id) { name tags } }"


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
    variables = {"id": '"1"'} if document == ONE else {}
    with pytest.raises(ValueError, match=rf"{problem}.*\[query.more\]"):
        registry.register_blok("cells", "<Html><div /></Html>", queries={"cells": Query(service="mikro", document=document, variables=variables, more=more)})
        registry.get_declared_bloks()


def test_more_may_name_a_nested_list(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.register_blok("cells", "<Html><div /></Html>", queries={"cells": Query(service="mikro", document=ONE, variables={"id": '"1"'}, more="dataset.tags")}, mode="strict")
    (blok,) = registry.get_declared_bloks().values()
    assert blok.queries[0].more == "dataset.tags"


def test_fetch_more_needs_a_query_that_declares_more(tmp_path: Path) -> None:
    with pytest.raises(BlokError) as caught:
        _registry(tmp_path).register_blok("cells", "<Html><Button onClick={queries.cells.fetch_more()} /></Html>", queries={"cells": Query(service="mikro", document=READ)})
    assert caught.value.code == "query.fetch_more"


def test_then_is_passed_to_a_query_as_an_ordinary_variable(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    document = "query($folder: ID) { datasets(folder: $folder) { id name } }"
    registry.register_blok("cells", "<Html><div /></Html>", queries={"cells": Query(service="mikro", document=document, variables={"folder": "then"})}, mode="strict")
    (blok,) = registry.get_declared_bloks().values()
    assert blok.queries[0].variables[0].value_path == "then"


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
