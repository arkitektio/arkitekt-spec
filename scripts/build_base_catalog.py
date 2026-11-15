"""Rebuild the base catalog from the contracts of `@arkitektio/bsx`.

    cd ~/Code/typescript/libraries/bsx/packages/bsx && pnpm build && pnpm spec:export
    python scripts/build_base_catalog.py ~/Code/typescript/libraries/bsx/packages/bsx/dist/spec-export

The base catalog (``catalogs/base@3.json``) is the spec's, and it is made of two things:

- What the spec alone says: the language's own elements and the parts of a host's interface
  (the ``language`` and ``run`` families), the family every component is listed under, and what
  no contract knows of a component (the ``kinds`` it belongs to, how it is drawn without a
  pointer, the minor that added it). These live in the file itself and are kept as they are.
- What a component takes and how it is documented, for every other family (``ui``, ``html``,
  ``shadcn``, ``structure``, ``scene``, ``logo``): written in `@arkitektio/bsx`'s contracts,
  which ``pnpm spec:export`` writes one file per family. This script takes those in.

A component the export adds joins the end of its family, at the catalog's next minor (its
``since``), and the catalog's ``minor`` is raised: a version only ever gains. One the export no
longer holds is refused: taking a component away is a new version of the catalog, not a rebuild.
Afterwards ``scripts/sync_standard_catalog.py`` writes the result back out.
"""

import argparse
import json
from pathlib import Path

from arkitekt_spec.blok.catalogs import parse_catalog
from arkitekt_spec.blok.vocabulary import load_vocabulary

BLOK = Path(__file__).resolve().parent.parent / "arkitekt_spec" / "blok"
SPEC_OWNED_FAMILIES = ("language", "run")
"""The families no contract of `@arkitektio/bsx` writes."""
SPEC_OWNED_FIELDS = ("group", "kinds", "touch", "since", "service")
"""What the spec says of a component beside its contract."""


def rebuild(base: dict[str, object], export: Path, *, first: bool = False) -> dict[str, object]:
    """``base`` with every family of ``export`` as its contracts say it now.

    ``first`` builds a catalog nothing was written for yet: what is added is there from the start.

    Raises:
        ValueError: If the export holds no families, redefines a spec-owned component, or no
            longer holds a component the catalog has.
    """
    current: list[dict[str, object]] = list(base["components"])  # type: ignore[arg-type]
    families: dict[str, dict[str, dict[str, object]]] = {}
    for file in sorted(export.glob("*.json")):
        payload = json.loads(file.read_text(encoding="utf-8"))
        families[str(payload["name"])] = {str(entry["name"]): entry for entry in payload["components"]}
    if not families:
        raise ValueError(f"{export} holds no exported families (run `pnpm spec:export` in @arkitektio/bsx)")
    owned = {str(entry["name"]) for entry in current if entry["group"] in SPEC_OWNED_FAMILIES}
    exported = {name: family for family, entries in families.items() for name in entries}
    clash = sorted(owned & set(exported))
    if clash:
        raise ValueError(f"the export redefines what the spec owns: {', '.join(clash)}")
    gone = sorted(str(entry["name"]) for entry in current if entry["group"] in families and entry["name"] not in exported)
    if gone:
        raise ValueError(f"the export no longer holds {', '.join(gone)}: taking a component away is a new version of the catalog")

    minor = int(base.get("minor", 0))  # type: ignore[call-overload]
    added_at = minor if first else minor + 1
    components: list[dict[str, object]] = []
    seen: set[str] = set()
    added: list[str] = []

    def merged(name: str, family: str, existing: dict[str, object] | None) -> dict[str, object]:
        described = families[family][name]
        entry: dict[str, object] = {"name": name, "group": family}
        entry.update({key: value for key, value in described.items() if key != "name"})
        if existing is not None:
            entry.update({key: existing[key] for key in SPEC_OWNED_FIELDS if key in existing and key != "group"})
        elif added_at > 0:
            entry["since"] = added_at
        return entry

    def flush(family: str) -> None:
        for name in families.get(family, {}):
            if name not in seen:
                seen.add(name)
                added.append(name)
                components.append(merged(name, family, None))

    last_of = {str(entry["group"]): index for index, entry in enumerate(current)}
    for index, entry in enumerate(current):
        name, family = str(entry["name"]), str(entry["group"])
        if family in families:
            seen.add(name)
            components.append(merged(name, exported[name], entry))
        else:
            components.append(entry)
        if last_of[family] == index:
            flush(family)
    for family in families:
        flush(family)
    rebuilt = dict(base)
    rebuilt["components"] = components
    if added and not first:
        rebuilt["minor"] = added_at
    return rebuilt


def main() -> None:
    """Rebuild the shipped base catalog from an export, and check it still parses."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("export", type=Path, help="@arkitektio/bsx's dist/spec-export: one <family>.json per family of the base catalog")
    parser.add_argument("--first", action="store_true", help="the catalog was not released yet: what is added is there from the start (no `since`, no new minor)")
    args = parser.parse_args()
    vocabulary = load_vocabulary()
    file = BLOK / vocabulary.bundled_catalogs[vocabulary.base_catalog]
    rebuilt = rebuild(json.loads(file.read_text(encoding="utf-8")), args.export, first=args.first)
    text = json.dumps(rebuilt, indent=1, ensure_ascii=False) + "\n"
    parse_catalog(text, str(file))
    file.write_text(text, encoding="utf-8")
    print(file)


if __name__ == "__main__":
    main()
