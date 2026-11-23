"""What a blok declares beside its tree, as an app writes it: its demo.

Everything a blok owns -- its locals, queries and subscriptions -- is declared in its tree
(``<local>``, ``<query>``, ``<subscription>``; :mod:`arkitekt_spec.blok.declared`).
"""

from collections.abc import Mapping

from arkitekt_spec.actions import BlokDemoInput
from arkitekt_spec.scalars import JSONSerializable


def demo_input(
    demo: "BlokDemoInput | Mapping[str, JSONSerializable] | None",
) -> BlokDemoInput | None:
    """``demo`` as the wire carries it: ``{deps, args, task, queries, subscriptions}``."""
    if demo is None or isinstance(demo, BlokDemoInput):
        return demo
    return BlokDemoInput.model_validate(dict(demo))


__all__ = ["demo_input"]
