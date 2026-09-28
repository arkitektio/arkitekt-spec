"""Base class for all structure errors."""

from arkitekt_spec.declare.errors import RekuestError


class SerializationError(RekuestError):
    """Base class for all serialization errors."""



class ExpandingError(SerializationError):
    """Base class for all expanding errors."""

    __port = None


class ShrinkingError(SerializationError):
    """Base class for all shrinking errors."""



class PortShrinkingError(ShrinkingError):
    """Base class for all port shrinking errors."""



class PortExpandingError(ExpandingError):
    """Base class for all port expanding errors."""



class StructureShrinkingError(PortShrinkingError):
    """Raised when a structure cannot be shrunk"""



class StructureExpandingError(PortExpandingError):
    """Raised when a structure cannot be expanded"""



class StructureRegistryError(Exception):
    """Base class for all structure registry errors."""



class StructureOverwriteError(StructureRegistryError):
    """Raised when a structure is attempted to be overwritten in the registry"""



class StructureDefinitionError(StructureRegistryError):
    """Raised when a structure was never defined in the registry"""



class StructureClientError(StructureRegistryError):
    """A structure expanded by a client has no client to expand with."""

