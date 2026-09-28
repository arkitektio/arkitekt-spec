"""Errors related to definition handling"""

from arkitekt_spec.declare.errors import RekuestError


class DefinitionError(RekuestError):
    """Base class for all definition errors"""



class NonSufficientDocumentation(DefinitionError):
    """Raised when we cannot infer sufficcient documentatoin"""
