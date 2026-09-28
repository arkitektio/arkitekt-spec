"""Test the PortInput class  and its validation logic."""

import pytest
from pydantic import ValidationError

from arkitekt_spec.actions import (
    ArgPortInput,
    PortKind,
)


def test_argport_input_errors() -> None:
    """Test invalid PortInput instances"""
    with pytest.raises(ValidationError):
        # kind is required and only accepts PortKind
        ArgPortInput(kind="lala")

    with pytest.raises(ValidationError):
        # identifier is required for STRUCTURE
        ArgPortInput(kind=PortKind.STRUCTURE, key="search")

    with pytest.raises(ValidationError):
        # child is required for List
        ArgPortInput(kind=PortKind.LIST, key="search")


def test_argport() -> None:
    """Test valid PortInput instances"""
    ArgPortInput(kind=PortKind.BOOL, key="search", nullable=False)
    ArgPortInput(kind=PortKind.STRING, key="search", nullable=False)

    ArgPortInput(
        kind=PortKind.STRUCTURE,
        identifier="hm/karl",
        key="search",
        nullable=False,
    )

    ArgPortInput(
        kind=PortKind.LIST,
        children=(ArgPortInput(key="0", kind=PortKind.BOOL, nullable=False),),
        nullable=False,
        key="search",
    )
