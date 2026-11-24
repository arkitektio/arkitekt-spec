"""One-off: rewrite catalogs typed by kinds (vocabulary 2) as catalogs typed by ports (vocabulary 3).

    python scripts/convert_catalogs_to_ports.py arkitekt_spec/blok/catalogs/base@4.json ...

Each prop's ``kind`` (+ ``values``, ``event``) becomes a ``port``:

- STRING, INT, FLOAT, BOOL: that kind; ``values`` become its choices;
- LIST, DICT: that kind with an open item (``...``);
- ANY: open (``port: null``);
- CALLBACK: its ``event`` fields as children; ASSET: ASSET;
- a ``bind`` prop: BINDING, its one child the kind it wrote.

The spec-owned families (``language``, ``run``) are typed by hand here (``HAND``); the others
are written again from `@arkitektio/bsx`'s contracts by ``build_base_catalog.py``, which types
what this leaves open. Operations (``base_v1.json``) convert the same way.
"""

import argparse
import json
from pathlib import Path

ITEM = "..."
TEXT = {
    "kind": "UNION",
    "children": [
        {"key": str(index), "kind": kind}
        for index, kind in enumerate(("STRING", "INT", "FLOAT", "BOOL"))
    ],
}
"""What a prop shown as text takes."""


def _list_of(kind: str) -> dict[str, object]:
    return {"kind": "LIST", "children": [{"key": ITEM, "kind": kind}]}


HAND: dict[tuple[str, str], dict[str, object] | None] = {
    ("args", "only"): {
        "kind": "UNION",
        "children": [
            {"key": "0", "kind": "STRING"},
            {"key": "1", **_list_of("STRING")},
        ],
    },
    ("args", "omit"): {
        "kind": "UNION",
        "children": [
            {"key": "0", "kind": "STRING"},
            {"key": "1", **_list_of("STRING")},
        ],
    },
    ("returns", "only"): {
        "kind": "UNION",
        "children": [
            {"key": "0", "kind": "STRING"},
            {"key": "1", **_list_of("STRING")},
        ],
    },
    ("returns", "omit"): {
        "kind": "UNION",
        "children": [
            {"key": "0", "kind": "STRING"},
            {"key": "1", **_list_of("STRING")},
        ],
    },
    ("#text", "value"): TEXT,
}
"""Spec-owned props a kind could not say: typed by hand."""


def _leaf(kind: str) -> dict[str, object] | None:
    if kind == "ANY":
        return None
    if kind in ("LIST", "DICT"):
        return {"kind": kind, "children": [{"key": ITEM, "kind": None}]}
    return {"kind": kind}


def prop_port(component: str, prop: dict[str, object]) -> dict[str, object] | None:
    """The port of one prop typed by a kind."""
    key, kind = str(prop["key"]), str(prop["kind"])
    if (component, key) in HAND:
        return HAND[(component, key)]
    if key == "bind":
        child = _leaf(kind) or {"kind": None}
        return {"kind": "BINDING", "children": [{"key": ITEM, **child}]}
    if kind == "CALLBACK":
        fields = prop.get("event") or []
        return {
            "kind": "CALLBACK",
            "children": [
                {
                    "key": field["key"],
                    **(_leaf(field["kind"]) or {"kind": None}),
                    **(
                        {"description": field["description"]}
                        if field.get("description")
                        else {}
                    ),
                }
                for field in fields  # type: ignore[union-attr]
            ],
        }
    port = _leaf(kind)
    values = prop.get("values")
    if port is not None and values:
        port["choices"] = [{"value": value} for value in values]  # type: ignore[union-attr]
    return port


def convert_prop(component: str, prop: dict[str, object]) -> dict[str, object]:
    if "port" in prop:
        return prop
    out = {
        key: value
        for key, value in prop.items()
        if key not in ("kind", "values", "event")
    }
    out["port"] = prop_port(component, prop)
    # Keep the order a reader expects: key, port, then the rest.
    return {"key": out.pop("key"), "port": out.pop("port"), **out}


def convert(payload: dict[str, object]) -> dict[str, object]:
    for component in payload.get("components", []):  # type: ignore[union-attr]
        component["props"] = [
            convert_prop(str(component["name"]), prop)
            for prop in component.get("props", [])
        ]
    for operation in payload.get("operations", []):  # type: ignore[union-attr]
        if isinstance(operation.get("returns"), str):
            operation["returns"] = _leaf(operation["returns"])
        operation["arguments"] = [
            {
                "key": argument["key"],
                "port": _leaf(argument["kind"]),
                **{k: v for k, v in argument.items() if k not in ("key", "kind")},
            }
            if "kind" in argument
            else argument
            for argument in operation.get("arguments", [])
        ]
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("files", type=Path, nargs="+")
    args = parser.parse_args()
    for file in args.files:
        raw = file.read_text(encoding="utf-8")
        indent = 2 if raw.startswith('{\n  "') else 1
        text = json.dumps(convert(json.loads(raw)), indent=indent, ensure_ascii=False)
        file.write_text(text + ("\n" if raw.endswith("\n") else ""), encoding="utf-8")
        print(file)


if __name__ == "__main__":
    main()
