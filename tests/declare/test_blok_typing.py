"""A blok's own UI state is typed: what ``local_state`` declares, and what its ``query`` reads write.

A ``query`` names its service (``schema="mikro"``); when the app knows that service's GraphQL
schema, the build checks the query against it, types the record its ``bind`` holds by the
query's selection, and freezes the service on the blok (its version and schema hash).
"""

import dataclasses
import warnings
from pathlib import Path
from typing import Annotated

import pytest
from pydantic import BaseModel

from arkitekt_spec.declare.app import AppRegistry
from arkitekt_spec.declare.blok.graphql import check_query, schema_hash
from arkitekt_spec.declare.blok.registry import build_declared_bloks
from arkitekt_spec.declare.blok.validate import idle_resource_record
from arkitekt_spec.declare.catalogs import CatalogWarning
from arkitekt_spec.declare.wiring import Alias, Require

pytest.importorskip("graphql")

SDL = """
type Dataset { id: ID! name: String! tags: [String!]! size: Int }
type Query {
  datasets(folder: ID): [Dataset!]!
  dataset(id: ID!): Dataset
}
type Mutation { deleteDataset(id: ID!): Boolean }
"""

READ = 'query { datasets { id name } }'


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


def _blok(read: str = READ, body: str = '<foreach items="@cells.data.datasets" let="#ds"><p text="@ds.name" /></foreach>') -> str:
    return f'<div><query schema="mikro" bind="cells" query="{read}" />{body}</div>'


# --------------------------------------------------------------------------- the query itself


def test_a_query_answers_with_its_selection() -> None:
    shape = check_query(SDL, "query Q($folder: ID) { datasets(folder: $folder) { id label: name size } }")
    assert shape.errors == ()
    assert shape.data is not None
    (datasets,) = shape.data.children or ()
    assert (datasets.key, datasets.kind, datasets.nullable) == ("datasets", "LIST", False)
    (item,) = datasets.children or ()
    assert [(child.key, child.kind, child.nullable) for child in item.children or ()] == [
        ("id", "STRING", False),
        ("label", "STRING", False),
        ("size", "INT", True),
    ]
    assert [(v.name, v.type, v.required) for v in shape.variables] == [("folder", "ID", False)]


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ("query { datasets { id nme } }", "Cannot query field 'nme'"),
        ("query { dataset { id } }", "Query.dataset(id:)' of type 'ID!' is required"),
        ("mutation { deleteDataset(id: 1) }", "never a mutation"),
        ("query { a } query { b }", "exactly one query"),
    ],
)
def test_a_query_the_schema_refuses(document: str, message: str) -> None:
    assert any(message in error for error in check_query(SDL, document).errors)


def test_a_schema_hash_ignores_formatting() -> None:
    assert schema_hash(SDL) == schema_hash("\n\n" + SDL.replace("  ", "    "))
    assert schema_hash(SDL).startswith("sha256:")


# --------------------------------------------------------------------------- the build


def test_a_checked_query_types_its_record_and_freezes_its_service(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.register_blok("cells", _blok(), mode="strict")
    (blok,) = build_declared_bloks(registry).values()

    (service,) = blok.services or ()
    assert (service.key, service.service, service.version) == ("mikro", "live.arkitekt.mikro", "8")
    assert service.schema_hash == schema_hash(SDL)
    # The record starts idle, so the tree can read it before the query answers.
    assert blok.demo_state["cells"] == idle_resource_record()


def test_a_read_of_what_the_query_does_not_select_is_refused(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.register_blok("cells", _blok(body='<foreach items="@cells.data.datasets" let="#ds"><p text="@ds.size" /></foreach>'))
    with pytest.raises(ValueError, match=r"'size' does not exist. Available keys: \['id', 'name'\]"):
        build_declared_bloks(registry)


def test_a_field_the_schema_lacks_warns_or_refuses(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.register_blok("loose", _blok(read="query { datasets { id nme } }", body=""))
    with pytest.warns(CatalogWarning, match="Cannot query field 'nme'"):
        build_declared_bloks(registry)

    strict = _registry(tmp_path)
    strict.register_blok("strict", _blok(read="query { datasets { id nme } }", body=""), mode="strict")
    with pytest.raises(ValueError, match="Cannot query field 'nme'"):
        build_declared_bloks(strict)


def test_a_service_without_a_known_schema_is_not_checked(tmp_path: Path) -> None:
    registry = _registry(tmp_path, schema=False)
    registry.register_blok("cells", _blok(body='<p text="@cells.data.anything" />'))
    with pytest.warns(CatalogWarning, match="knows no schema for the 'mikro' service"):
        (blok,) = build_declared_bloks(registry).values()
    # Still declared, so the server lets the query run; only its schema is unknown.
    (service,) = blok.services or ()
    assert (service.key, service.schema_hash) == ("mikro", None)


def test_two_reads_on_one_path_are_refused(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    tree = f'<div><query schema="mikro" bind="cells" query="{READ}" /><query schema="mikro" bind="cells" query="{READ}" /></div>'
    registry.register_blok("twice", tree)
    with pytest.raises(ValueError, match="already writes 'cells'"):
        build_declared_bloks(registry)


def test_a_control_bound_into_a_record_is_refused(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.register_blok("overwrite", _blok(body='<Input bind="cells.data" />'))
    with pytest.raises(ValueError, match="inside the record query"):
        build_declared_bloks(registry)


# --------------------------------------------------------------------------- local_state


@dataclasses.dataclass
class Form:
    iterations: int = 15
    label: str = "run"
    tags: list[str] = dataclasses.field(default_factory=list)


class Filter(BaseModel):
    threshold: float = 0.5


def test_a_typed_local_state_starts_from_its_defaults_and_checks_its_paths() -> None:
    registry = AppRegistry()
    registry.register_blok("form", '<div><Input bind="form.iterations" /><p text="@filter.threshold" /></div>', local_state={"form": Form, "filter": Filter(threshold=0.9)})
    (blok,) = build_declared_bloks(registry).values()
    assert blok.demo_state["form"] == {"iterations": 15, "label": "run", "tags": []}
    assert blok.demo_state["filter"] == {"threshold": 0.9}

    typo = AppRegistry()
    typo.register_blok("typo", '<div><Input bind="form.iterationz" /></div>', local_state={"form": Form})
    with pytest.raises(ValueError, match=r"'iterationz' does not exist. Available keys: \['iterations', 'label', 'tags'\]"):
        build_declared_bloks(typo)


def test_a_class_without_defaults_needs_an_instance() -> None:
    @dataclasses.dataclass
    class Bare:
        x: int

    with pytest.raises(ValueError, match="every field of Bare needs a default"):
        AppRegistry().register_blok("bare", "<div />", local_state={"form": Bare})


def test_a_plain_local_state_is_typed_by_its_values() -> None:
    registry = AppRegistry()
    registry.register_blok("plain", '<p text="@form.missing" />', local_state={"form": {"iterations": 15}})
    with pytest.raises(ValueError, match=r"'missing' does not exist. Available keys: \['iterations'\]"):
        build_declared_bloks(registry)

    # A value that says nothing (None) keeps the root readable as before.
    loose = AppRegistry()
    loose.register_blok("loose", '<p text="@pick.anything" />', local_state={"pick": None})
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        build_declared_bloks(loose)


def test_a_federation_subgraph_schema_is_read_as_served() -> None:
    """A subgraph names directives (``@key``) it does not declare; the schema is what the service serves."""
    subgraph = 'type Dataset @key(fields: "id") { id: ID! name: String! }\ntype Query { datasets: [Dataset!]! }'
    assert check_query(subgraph, READ).errors == ()
    assert schema_hash(subgraph).startswith("sha256:")


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
