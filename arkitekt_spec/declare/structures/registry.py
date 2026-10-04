"""The structure registry is a registry for all structures that are used in the system."""

import dataclasses
import inspect
from collections.abc import Callable, Iterable, Mapping
from enum import Enum
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    NoReturn,
    TypeVar,
    cast,
    dataclass_transform,
    get_origin,
    get_type_hints,
)

if TYPE_CHECKING:
    from arkitekt_spec.declare.agents.types import BoundApp
    from arkitekt_spec.declare.declare import DeclaredAgentProtocol

from functools import partial

import inflection
from pydantic import BaseModel, ConfigDict, Field

from arkitekt_spec.actions import (
    ArgPortInput,
    AssignWidgetInput,
    ChoiceInput,
    EffectInput,
    PortKind,
    ProvidesInput,
    RequiresInput,
    ReturnPortInput,
    ReturnWidgetInput,
    ValidatorInput,
)
from arkitekt_spec.declare.agents.errors import StateRequirementsNotMet
from arkitekt_spec.declare.agents.types import resolve_service_clients, unwrap_injectable
from arkitekt_spec.declare.structures.convert import (
    cls_to_identifier,
    fullfilled_enum_from_cls,
    fullfilled_enum_from_literal,
    fullfilled_structure_from_cls,
    is_global_structure,
    is_literal,
    make_enum_converter,
    model_identifier,
)
from arkitekt_spec.declare.structures.model import ensure_model_dataclass, model_field
from arkitekt_spec.declare.structures.utils import build_instance_predicate

from .errors import (
    StructureClientError,
    StructureDefinitionError,
    StructureOverwriteError,
    StructureRegistryError,
)
from .types import (
    ContextDeclaration,
    Expander,
    ExpanderT,
    FullFilledEnum,
    FullFilledMemoryStructure,
    FullFilledModel,
    FullFilledStructure,
    FullFilledType,
    ManyExpander,
    Predicator,
    Shrinker,
    StateDeclaration,
    is_valid_identifier,
)

T = TypeVar("T")


#: Which identifier map each fullfilled type lives in, and which port kind it yields.
_IDENTIFIER_MAP_FOR: dict[type, str] = {
    FullFilledModel: "identifier_model_map",
    FullFilledEnum: "identifier_enum_map",
    FullFilledMemoryStructure: "identifier_memory_structure_map",
    FullFilledStructure: "identifier_structure_map",
}
_PORT_KIND_FOR: dict[type, PortKind] = {
    FullFilledModel: PortKind.MODEL,
    FullFilledEnum: PortKind.ENUM,
    FullFilledMemoryStructure: PortKind.MEMORY_STRUCTURE,
    FullFilledStructure: PortKind.STRUCTURE,
}


def _display_name(obj: object) -> str:
    """How a registered type is named in an error message.

    Read defensively rather than as ``obj.__qualname__``: what a caller registers need not be a
    class. A ``Literal[...]`` form and a union are both accepted here and neither carries one.
    """
    qualname = getattr(obj, "__qualname__", None)
    return qualname if isinstance(qualname, str) else repr(obj)


def _qualified(cls: object) -> str:
    module = getattr(cls, "__module__", None)
    return f"{module if isinstance(module, str) else '?'}.{_display_name(cls)}"


def _travelling_class(
    expand: Callable[..., Any], identifier: str, cls: type[object] | None
) -> type[object]:
    """The class a structure's expander returns, or the one declared for it.

    Raises:
        StructureDefinitionError: If neither is a class, or the two disagree.
    """
    name = _display_name(expand)
    try:
        returned = get_type_hints(expand).get("return")
    except (NameError, TypeError) as e:
        if cls is not None:
            return cls
        raise StructureDefinitionError(
            f"'{identifier}': the return annotation of {name} cannot be resolved "
            f"({e}), so the class that travels is unknown. Pass cls=."
        ) from e
    # ``Any`` has been a class to ``inspect`` since 3.11; it names none.
    returned_cls: type[object] | None = (
        returned if inspect.isclass(returned) and returned is not Any else None
    )
    if cls is None:
        if returned_cls is None:
            raise StructureDefinitionError(
                f"'{identifier}': {name} does not say what it returns"
                f"{'' if returned is None else f' ({returned!r} is not a class)'}, "
                "so the class that travels is unknown. Annotate its return type "
                "with the class, or pass cls=."
            )
        return returned_cls
    if returned_cls is not None and not issubclass(returned_cls, cls):
        raise StructureDefinitionError(
            f"'{identifier}' is declared for {_qualified(cls)}, but {name} "
            f"returns {_qualified(returned_cls)}. One structure, one class."
        )
    return cls


def _same_class(one: type[Any], other: type[Any]) -> bool:
    """The same class, or the same class re-created by re-importing its module."""
    return one is other or _qualified(one) == _qualified(other)


def _declared_by(structure: FullFilledStructure) -> str:
    return f" (service '{structure.service}')" if structure.service else ""


def _package_of(cls: type[Any]) -> str:
    return (getattr(cls, "__module__", "") or "").split(".")[0]


def _refuse_undeclared_structure(cls: type[Any]) -> None:
    """Refuse to invent a wire name for a type that knows how to fetch itself.

    Such a class (a generated ``ArrayDataset``, say) passes for a structure, and
    used to be registered under whatever ``get_identifier()`` says: its bare
    typename. The server refuses that, but only once the agent registers, with
    nothing pointing back here. The real cause is always that nothing declared
    it, so say that.
    """
    identifier = cls.get_identifier()
    if is_valid_identifier(identifier):
        return
    package = _package_of(cls)
    raise StructureDefinitionError(
        f"{_qualified(cls)} can expand and shrink itself, but nothing declared it "
        f"as a structure, and its own name for itself, '{identifier}', is not one "
        "it can travel under: the rekuest server only accepts '@package/key'. "
        f"If it comes from a service package, that service declares it: is "
        f"'{package}' one of this app's services (`app.service(...)`)? "
        "If it is your own class, return '@yourapp/name' from `get_identifier`, or "
        "declare it with `structure_registry.register_as_structure`."
    )


def _refuse_undeclared_fragment(cls: type[Any]) -> None:
    """Refuse to shelve a generated GraphQL fragment as a memory structure.

    A turms fragment that cannot fetch itself (``LensDataset``, nested in
    another type) is not a structure. It used to be demoted silently to a memory
    structure: an object parked in this agent's memory, unusable by any other
    app, under a name like ``@mikro.api.schema/lensdataset``. That is never what
    a signature naming it meant.
    """
    meta = getattr(cls, "Meta", None)
    if meta is None or not hasattr(meta, "document"):
        return
    raise StructureDefinitionError(
        f"{_qualified(cls)} is a generated GraphQL fragment that cannot be fetched "
        "by id, so it cannot travel between apps, and it was about to be kept as a "
        "memory structure of this agent instead. Use the type it is a part of "
        "(the one with a `get_...` query), or declare how to expand it with "
        "`structure_registry.register_as_structure`."
    )


class _ClientsAsApp:
    """A :class:`~rekuest.agents.types.BoundApp` over a plain ``{service: client}`` map.

    Binding a registry on its own -- in a test, or anywhere outside a run -- is a
    supported thing to do, and a bare mapping is the natural way to say it. This
    is what lets :meth:`StructureRegistry.bind` take either without keeping two
    ways to find a client: the mapping answers by name directly, and by class by
    scanning, which is what an app of a handful of clients does anyway.
    """

    def __init__(self, clients: Mapping[str, Any]) -> None:
        self.clients = clients
        self.services = clients

    def get(self, key: type) -> Any | None:
        """The client of class ``key``, or ``None``."""
        for client in self.clients.values():
            if isinstance(client, key):
                return client
        return None


def _as_bound_app(app: "BoundApp | Mapping[str, Any]") -> "BoundApp":
    """Take a run, or a bare ``{service: client}`` mapping, as something to bind from."""
    if isinstance(app, Mapping):
        return cast("BoundApp", _ClientsAsApp(app))
    return app


def injected_clients_of(
    function: Callable[..., Any], identifier: str, role: str
) -> dict[str, type]:
    """The clients ``function`` asks for by annotation, read where it is written.

    A structure's expander names the clients it needs the same way an action does
    -- ``expand(id, mikro: Mikro)``. The first parameter is the id; every one
    after it is a client.

    Only the *shape* is checked here. Whether a declared service actually builds
    each class is settled later, by :meth:`AppRegistry.validate`, because a
    structure is declared when its package is imported -- before any service
    exists to be asked.

    Args:
        function: The expander or shrinker.
        identifier: The structure it belongs to, for the message.
        role: What the function is ("expand"/"shrink"), for the message.

    Returns:
        Parameter name to the class it wants, empty when it asks for nothing.

    Raises:
        StructureDefinitionError: If it is a lambda, its annotations cannot be
            read, or a parameter past the first is unannotated.
    """
    if getattr(function, "__name__", "") == "<lambda>":
        raise StructureDefinitionError(
            f"'{identifier}': the {role} is a lambda, which carries no annotations "
            "-- so there is no way to see which clients it needs. Use a named "
            "function with annotated parameters."
        )

    try:
        hints = get_type_hints(function, include_extras=True)
    except Exception as e:
        raise StructureDefinitionError(
            f"'{identifier}': the {role} function's annotations could not be read "
            f"({e}). Its parameters say which clients a run must inject."
        ) from e

    injects: dict[str, type] = {}
    for name in list(inspect.signature(function).parameters)[1:]:
        annotation = hints.get(name)
        if annotation is None:
            raise StructureDefinitionError(
                f"'{identifier}': the {role} function's '{name}' is unannotated. "
                "Every parameter after the id is a client a run injects, named by "
                "its class (`mikro: Mikro`)."
            )
        if not isinstance(annotation, type):
            raise StructureDefinitionError(
                f"'{identifier}': the {role} function wants {annotation!r} as "
                f"'{name}', which is not a class. A run injects clients by class."
            )
        injects[name] = annotation
    return injects


class StructureRegistry(BaseModel):
    """A registry for structures.

    Structure registries are used to provide a mapping from "identifier" to python
    classes and vice versa.

    When an actors receives a request from the arkitekt server with a specific
    id Y and identifier X, it will look up the structure registry for the identifier X
    and use the corresponding python class to deserialize the data.

    The structure registry is also used to provide a mapping from python classes to identifiers

    **Nothing is registered automatically.** Anything that carries an object --
    a structure fetched by id, or a memory structure kept on the agent's shelve --
    must be registered before a port can name it, either by the service that
    declares it or by hand (:meth:`register_as_structure`,
    :meth:`register_as_memory_structure`, :meth:`register_as_model`). A class that
    was never registered raises rather than being invented on the spot, which is
    what used to turn a misspelt annotation into an unusable memory structure.

    The one exception is enums, including ``Literal[...]``: they travel by value
    and every part of them is read off the annotation, so there is nothing to
    declare. :meth:`derive_enum` builds those on demand.

    That exception is also why this registry is not frozen with its app. Nothing
    *else* writes to it after registration: shrinking and expanding are driven
    entirely by the ports of the definition and only ever look up here, memory
    structures included.
    """

    identifier_structure_map: dict[str, FullFilledStructure] = Field(
        default_factory=dict, exclude=True
    )
    identifier_enum_map: dict[str, FullFilledEnum] = Field(
        default_factory=dict, exclude=True
    )
    identifier_memory_structure_map: dict[str, FullFilledMemoryStructure] = Field(
        default_factory=dict, exclude=True
    )
    identifier_model_map: dict[str, FullFilledModel] = Field(
        default_factory=dict, exclude=True
    )
    clients: dict[str, type] = Field(default_factory=dict, exclude=True)
    """What each registered service returns, by service name: the client classes.

    Written by the app registry when a service is declared or taken in. A
    parameter annotated with one of these (``mikro: Mikro``), on an action, a
    hook or an expander, is handed the built client instead of becoming a port.
    """
    cls_fullfilled_type_map: dict[Any, FullFilledType] = Field(
        default_factory=dict, exclude=True
    )
    """The fullfilled type of each port annotation.

    Keyed by the annotation as written on the function: a class, or a
    ``Literal[...]`` (which is not a class, hence ``Any``). A Literal-derived
    enum is keyed by the Literal itself, so the same Literal on two functions
    resolves to the same enum.
    """
    # Typed loosely: pydantic would need the protocol class at import to build
    # the field, and `rekuest.declare` imports this module.
    protocols: dict[type[Any], Any] = Field(default_factory=dict, exclude=True)
    states: dict[type[Any], StateDeclaration] = Field(
        default_factory=dict, exclude=True
    )
    contexts: dict[type[Any], ContextDeclaration] = Field(
        default_factory=dict, exclude=True
    )
    app_contexts: dict[type[Any], str] = Field(default_factory=dict, exclude=True)
    """The app-context classes this app declared, by class: the name each is known by.

    Written by :meth:`app_context`. The app context is whatever object the caller
    passed to ``run(context=...)``; a hook annotated with one of these classes is
    handed it, and a hook that runs after startup checks it is an instance.
    """
    """The contexts this app declared, by class: the name and the locks.

    Written by :meth:`context`. A parameter annotated with one of these classes
    is handed the agent's context of that name; a startup hook returning one
    publishes it under that name. Nothing is written on the class.
    """
    """The states this app declared, by class: interface, schema and locks.

    Written by the app registry's ``register_state``. A parameter annotated with
    one of these classes is handed that state (or a read-only view of it), and
    an agent adopting an instance makes it evented with these rules. Nothing is
    written on the class, so one class can be a state of any number of apps.
    """
    """The :class:`~rekuest.declare.DeclaredAgentProtocol` this app declared, by class.

    Written by :meth:`declare`. A parameter annotated with one of these classes
    is handed a proxy to the remote agent instead of becoming a port; a blok
    names one under a key. Each holds its demands with ports already built
    against this registry, so nothing here is looked up per call.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def _lookup(self, map_name: str, identifier: str) -> Any:
        """Look an identifier up in one of the identifier maps.

        There is no chain to fall back to: a registry holds exactly what its own
        app's services declared, and nothing else.
        """
        own: dict[str, Any] = getattr(self, map_name)
        if identifier in own:
            return own[identifier]
        raise KeyError(identifier)

    def get_fullfilled_structure(self, identifier: str) -> FullFilledStructure:
        """Get the fullfilled structure for a given identifier."""
        return self._lookup("identifier_structure_map", identifier)

    def get_fullfilled_enum(self, identifier: str) -> FullFilledEnum:
        """Get the fullfilled enum for a given identifier."""
        return self._lookup("identifier_enum_map", identifier)

    def get_fullfilled_model(self, identifier: str) -> FullFilledModel:
        """Get the fullfilled model for a given identifier."""
        return self._lookup("identifier_model_map", identifier)

    def get_fullfilled_memory_structure(
        self, identifier: str
    ) -> FullFilledMemoryStructure:
        """Get the fullfilled memory structure for a given identifier."""
        return self._lookup("identifier_memory_structure_map", identifier)

    def declared_as_another(self, value: object, identifier: str) -> FullFilledStructure | None:
        """The structure ``value`` was declared as, when that is not ``identifier``.

        A structure travels as its id, and by default that is just ``value.id``:
        a dataset returned where a lens is annotated would be sent as a lens with
        the dataset's id. ``None`` for a value of the right structure, and for
        one this app never declared (which may be anything carrying an id).
        """
        try:
            found = self.find_for_cls(type(value))
        except TypeError:  # unhashable class
            return None
        if isinstance(found, FullFilledStructure) and found.identifier != identifier:
            return found
        return None

    def find_for_cls(self, cls: type[Any]) -> FullFilledType | None:
        """Find the fullfilled type registered for a class, or ``None``.

        Unlike :meth:`get_fullfilled_type_for_cls` this neither derives an enum
        nor raises. A parameterised annotation (``NDArray[np.float64]``,
        ``Box[int]``) finds what its origin class was registered as.
        """
        found = self.cls_fullfilled_type_map.get(cls)
        if found is None:
            origin = get_origin(cls)
            if isinstance(origin, type):
                found = self.cls_fullfilled_type_map.get(origin)
        return found

    def derive_enum(self, cls: type[Any]) -> FullFilledType:
        """Derive an enum port from an ``Enum`` subclass or a ``Literal[...]``.

        These are the only two things a registry still builds on its own, and
        they are not really *registration*: an enum travels by value, so every
        part of it -- the choices, the converter, the predicate -- is read off
        the annotation. There is nothing to configure and so nothing to declare.

        Everything that carries an object rather than a value must be registered
        ahead of time; see :meth:`get_fullfilled_type_for_cls`.

        Args:
            cls (Type): The enum class or ``Literal[...]`` annotation.

        Returns:
            FullFilledType: The fullfilled enum that was created.

        Raises:
            StructureDefinitionError: If the class could not be converted.
        """
        fullfilled_type = (
            fullfilled_enum_from_literal(cls)
            if is_literal(cls)
            else fullfilled_enum_from_cls(cls)
        )
        self.fullfill_registration(fullfilled_type)
        return fullfilled_type

    def _refuse_unregistered(self, cls: type[Any]) -> NoReturn:
        """Explain why ``cls`` cannot be used as a port, in its own terms.

        There are three ways to arrive here and they need three different
        answers, because the fix is different each time.
        """
        if is_global_structure(cls):
            # It can fetch itself, so it *looks* ready. What is missing is the
            # declaration that says which service expands it.
            _refuse_undeclared_structure(cls)
            raise StructureRegistryError(
                f"{_qualified(cls)} can expand and shrink itself, but nothing "
                f"declared it as a structure of this app. A service package "
                f"declares its own ({_package_of(cls)} would declare this one) -- "
                "is it one of this app's services? If it is your own class, "
                "register it with `app.register_structure(...)`."
            )

        # A generated fragment has its own explanation and is never a memory
        # structure, whatever the caller intended.
        _refuse_undeclared_fragment(cls)

        if dataclasses.is_dataclass(cls):
            name = getattr(cls, "__name__", cls)
            raise StructureRegistryError(
                f"{_qualified(cls)} is a dataclass nothing declared as a model of "
                f"this app. If its fields travel by value, declare it: "
                f"`app.model({name})` -- a model used inside another model has to "
                "be declared too. If it is an object this agent keeps to itself: "
                f"`app.register_memory_structure({name})`."
            )

        raise StructureRegistryError(
            f"{_qualified(cls)} is not registered, and nothing is registered "
            "automatically any more.\n"
            "  If it is a client (`mikro: Mikro`), register the service that "
            "builds it first: `App(services=[mikro_service])`.\n"
            "  If it is an object this agent should keep to itself, say so:\n"
            f"      app.register_memory_structure({getattr(cls, '__name__', cls)})\n"
            "  If it should travel between apps, it needs an expander and a "
            "shrinker:\n"
            '      app.register_structure(Cls, "@lib/key", expand=fetch_it)\n'
            "Registering it silently is what this replaces: a typo in an "
            "annotation used to become a memory structure that no other app "
            "could ever read."
        )

    def get_identifier_for_cls(self, cls: type[Any]) -> str:
        """Get the identifier for a given class.

        This will use the structure registry to find the correct
        identifier for the given class.

        Args:
            cls (Type): The class to get the identifier for.
        Returns:
            str: The identifier for the given class.
        Raises:
            StructureRegistryError: If the class is not registered.
        """
        found = self.find_for_cls(cls)
        if found is None:
            raise StructureRegistryError(f"Identifier for {cls} is not registered")
        return found.identifier

    def register_as_model(
        self,
        cls: type[Any],
        identifier: str,
        predicate: Predicator | None = None,
        description: str | None = None,
    ) -> None:
        """Register a class as a model.

        Raises:
            StructureDefinitionError: If the identifier is not ``@package/key``,
                which the server would refuse when the agent registers.
        """
        if not is_valid_identifier(identifier):
            raise StructureDefinitionError(
                f"The model {cls.__qualname__} cannot travel as {identifier!r}: an identifier "
                f"reads '@package/key', e.g. '@myapp/{inflection.underscore(cls.__name__)}'."
            )

        fullfile_type = FullFilledModel(
            cls=cls,
            identifier=identifier,
            predicate=predicate or build_instance_predicate(cls),
            description=description,
        )

        self.fullfill_registration(fullfile_type)

    def register_as_enum(
        self,
        cls: type[Any],
        identifier: str,
        choices: list[ChoiceInput],
        description: str | None = None,
        default_widget: AssignWidgetInput | None = None,
        default_returnwidget: ReturnWidgetInput | None = None,
    ) -> None:
        """Register a class as an enum."""
        fullfile_type = FullFilledEnum(
            cls=cls,
            identifier=identifier,
            choices=choices,
            members=dict(cls.__members__),
            predicate=build_instance_predicate(cls),
            description=description,
            default_widget=default_widget,
            convert_default=make_enum_converter(cls),
            default_returnwidget=default_returnwidget,
        )

        self.fullfill_registration(fullfile_type)

    def register_from_protocol(self, cls: type[Any]) -> FullFilledStructure:
        """Register a class that already carries its own structure protocol.

        For a class that defines ``get_identifier``, ``ashrink`` and ``aexpand``
        itself: everything needed is on the class, so nothing has to be repeated
        here. This is the one-liner that replaces what auto-registration used to
        do behind your back -- the difference being that it is now asked for.

        Args:
            cls: The class to register. Its ``get_identifier()`` must return an
                identifier the server accepts (``@package/key``).

        Returns:
            FullFilledStructure: What was registered.

        Raises:
            StructureDefinitionError: If the class does not carry the protocol, or
                names itself something that cannot travel.
        """
        if not hasattr(cls, "get_identifier"):
            raise StructureDefinitionError(
                f"{_qualified(cls)} does not carry the structure protocol: it has "
                "no `get_identifier`, so there is nothing to take it at its word "
                "about. Use `register_as_structure(cls, identifier, aexpand=..., "
                "ashrink=...)` to say how it travels, or "
                "`register_as_memory_structure(cls)` if it should stay on this agent."
            )
        _refuse_undeclared_structure(cls)
        fullfilled_type = fullfilled_structure_from_cls(cls)
        self.fullfill_registration(fullfilled_type)
        return fullfilled_type

    def register_as_memory_structure(
        self,
        cls: type[Any],
        identifier: str | None = None,
        description: str | None = None,
        predicate: Predicator | None = None,
    ) -> FullFilledMemoryStructure:
        """Register a class whose instances stay on this agent's shelve.

        A memory structure never leaves the agent: an assignment gets a drawer id
        pointing at an object parked in this process, which is why it needs no
        expander and no shrinker. It also means nothing else can read it, so it
        has to be asked for rather than inferred -- a misspelt annotation used to
        become one of these silently.

        Args:
            cls: The class to keep on the shelve.
            identifier: What to call it. Defaults to a name derived from the class
                (``numpy.ndarray`` -> ``@numpy/ndarray``). It is internal
                bookkeeping, since a memory structure has nowhere to travel to.
            description: What it is, for the UI.
            predicate: How to recognise one. Defaults to an isinstance check.

        Returns:
            FullFilledMemoryStructure: What was registered.
        """
        fullfilled_type = FullFilledMemoryStructure(
            cls=cls,
            identifier=identifier or cls_to_identifier(cls),
            predicate=predicate or build_instance_predicate(cls),
            description=description,
        )
        self.fullfill_registration(fullfilled_type)
        return fullfilled_type

    def register_as_structure(
        self,
        cls: type[object],
        identifier: str,
        aexpand: Expander,
        ashrink: Shrinker | None = None,
        aexpand_many: ManyExpander | None = None,
        predicate: Callable[[Any], bool] | None = None,
        convert_default: Callable[[Any], str] | None = None,
        description: str | None = None,
        default_widget: AssignWidgetInput | None = None,
        default_returnwidget: ReturnWidgetInput | None = None,
    ) -> FullFilledStructure:
        """Register a class as a structure.

        This will create a new structure and register it in the registry.
        This function should be called when you want to specifically register a class
        as a structure. This will mainly be used for classes that are global
        and should be registered as a structure.

        Args:
            cls (Type): The class to register
            identifier (str): The identifier of the class. This should be unique and will be send to the rekuest server
            scope (PortScope, optional): The scope of the port. Defaults to PortScope.LOCAL.
            aexpand (Callable[ [ str, ], Awaitable[Any], ] | None, optional): An expander (needs to be set for a GLOBAL). Defaults to None.
            ashrink (Callable[ [ Any, ], Awaitable[str], ] | None, optional): A shrinker (needs to be set for a GLOBAL). Defaults to None.
            predicate (Callable[[Any], bool] | None, optional): A predicate that will check if its an instance of this type (will autodefault to the issinstance check). Defaults to None.
            convert_default (Callable[[Any], str] | None, optional): A way to convert the default. Defaults to None.
            default_widget (Optional[AssignWidgetInput], optional): A widget that will be used as a default. Defaults to None.
            default_returnwidget (Optional[ReturnWidgetInput], optional): A return widget that will be used as a default. Defaults to None.

        Returns:
            FullFilledStructure: The fullfilled structure that was created
        """

        injects = injected_clients_of(aexpand, identifier, "expand")
        if ashrink is not None:
            injects.update(injected_clients_of(ashrink, identifier, "shrink"))
        if aexpand_many is not None:
            injects.update(injected_clients_of(aexpand_many, identifier, "expand_many"))
        # A run can only hand out what a registered service returns, so the
        # service comes first; refused here rather than mid-assignment. The
        # service whose client the expander asks for is the one that owns the
        # structure -- the annotation says so, the identifier only hints.
        owner: str | None = None
        for key, wanted in sorted(injects.items()):
            if not self.is_client(wanted):
                raise StructureDefinitionError(
                    f"'{identifier}': its expander wants a {wanted.__name__} as "
                    f"'{key}', but no registered service returns one. Declare the "
                    "service that builds it first (`@registry.service()` on "
                    "`def mikro(...) -> Mikro`)."
                )
            if owner is None:
                owner = next(
                    name
                    for name, returned in self.clients.items()
                    if issubclass(returned, wanted)
                )

        fs = FullFilledStructure(
            cls=cls,
            identifier=identifier,
            aexpand=aexpand,
            ashrink=ashrink,
            aexpand_many=aexpand_many,
            injects=injects,
            service=owner,
            declared=owner is not None,
            description=description,
            convert_default=convert_default,
            predicate=predicate or build_instance_predicate(cls),
            default_widget=default_widget,
            default_returnwidget=default_returnwidget,
        )
        self.fullfill_registration(fs)
        return fs

    def merge(
        self,
        other: "StructureRegistry",
        service: "str | Callable[[FullFilledStructure], str | None] | None" = None,
    ) -> "StructureRegistry":
        """Take everything ``other`` holds into this registry, in place.

        Args:
            other: The registry to take. It is not changed.
            service: The service its structures belong to, stamped on each -- a
                name, or a function naming the owner of each structure (``None``
                for one no service owns). The identifier only hints at it, and
                the hint is wrong where it matters -- unlok's structure is
                ``@lok/service``.

        Returns:
            This registry.

        Raises:
            StructureDefinitionError: If the two claim one identifier for
                different classes. An identifier is a wire contract.
        """
        for structure in other.identifier_structure_map.values():
            owner = service(structure) if callable(service) else service
            taken = (
                structure
                if owner is None
                else structure.model_copy(update={"service": owner, "declared": True})
            )
            self.fullfill_registration(taken)
        for memory in other.identifier_memory_structure_map.values():
            self.fullfill_registration(memory)
        for model in other.identifier_model_map.values():
            self.fullfill_registration(model)
        for enum in other.identifier_enum_map.values():
            self.fullfill_registration(enum)
        for cls, protocol in other.protocols.items():
            self._take_protocol(cls, protocol)
        for cls, declaration in other.states.items():
            self.states.setdefault(cls, declaration)
        for cls, context in other.contexts.items():
            self.contexts.setdefault(cls, context)
        for cls, name in other.app_contexts.items():
            self._refuse_second_app_context(cls)
            self.app_contexts.setdefault(cls, name)
        return self

    def declare(
        self,
        app: str | None = None,
        *,
        auto_resolvable: bool = False,
        min: int | None = None,
        max: int | None = None,
        version: str | None = None,
    ) -> "Callable[[type[T]], type[T]]":
        """Declare a protocol class: what this app demands of another app.

        The class is inspected in two passes -- public methods become action
        demands, public annotated attributes become state demands (each
        annotated with a class whose annotations are the state's fields) -- and
        every demand's ports are built now, against this registry. The class itself is returned unchanged; the resulting
        :class:`~rekuest.declare.DeclaredAgentProtocol` is kept here under it.

        Args:
            app: The remote app the protocol is directed at, if any.
            auto_resolvable: Whether any matching agent may be assigned
                automatically.
            min: Minimum viable number of matching agents.
            max: Maximum viable number of matching agents.
            version: The protocol's version.

        Returns:
            A class decorator returning the class.

        Raises:
            StructureDefinitionError: If the class was declared here already
                with different demands.
            DefinitionError: If a demand names a class this registry cannot
                make a port of.

        Examples:
            ::

                class CameraState:
                    connected: bool

                @app.declare(app="lab")
                class CameraProtocol:
                    state: CameraState

                    async def snap(self, exposure_ms: float) -> bytes: ...
        """
        from arkitekt_spec.declare.declare import DeclaredAgentProtocol

        def decorate(cls: type[T]) -> type[T]:
            self._take_protocol(
                cls,
                DeclaredAgentProtocol(
                    cls,
                    self,
                    app=app,
                    auto_resolvable=auto_resolvable,
                    min=min,
                    max=max,
                    version=version,
                ),
            )
            return cls

        return decorate

    def _take_protocol(
        self, cls: type[Any], protocol: "DeclaredAgentProtocol[Any]"
    ) -> None:
        """Keep ``protocol`` under ``cls``; refuse one that contradicts what is kept.

        The same class may be declared twice, or come in from two merged package
        registries, as long as it demands the same thing: what a dependency
        demands is a wire contract, so two different contracts under one class
        cannot both be served.
        """
        existing = self.protocols.get(cls)
        if existing is None or existing is protocol:
            self.protocols[cls] = protocol
            return
        if existing.to_dependency_input("_") != protocol.to_dependency_input("_"):
            raise StructureDefinitionError(
                f"{cls.__qualname__} is declared here already, with different demands. "
                "A protocol class means one thing to an app."
            )

    def protocol_for(self, cls: Any) -> "DeclaredAgentProtocol[Any]":
        """The declared protocol kept under ``cls``.

        Raises:
            KeyError: If ``cls`` was not declared on this app.
        """
        try:
            return self.protocols[cls]
        except (KeyError, TypeError):
            shown = _display_name(cls)
            raise KeyError(
                f"{shown} is not a protocol this app declared. Declare it with "
                f"`@app.declare(...)` before naming it."
            ) from None

    def is_protocol(self, annotation: Any) -> bool:
        """Whether ``annotation`` is a class this app declared a protocol for."""
        try:
            return annotation in self.protocols
        except TypeError:  # unhashable annotation
            return False

    def is_state(self, annotation: Any) -> bool:
        """Whether ``annotation`` is a class this app registered as a state."""
        try:
            return annotation in self.states
        except TypeError:  # unhashable annotation
            return False

    def state_for(self, cls: Any) -> StateDeclaration:
        """What this app declared about the state class ``cls``.

        Raises:
            KeyError: If ``cls`` is not a state of this app.
        """
        try:
            return self.states[cls]
        except (KeyError, TypeError):
            shown = _display_name(cls)
            raise KeyError(
                f"{shown} is not a state this app registered. Register it with "
                "`@app.state` before naming it."
            ) from None

    def register_context(
        self,
        cls: type[Any],
        *,
        name: str | None = None,
        locks: Iterable[str] | None = None,
    ) -> ContextDeclaration:
        """Record ``cls`` as a context of this app.

        Args:
            cls: The context class.
            name: The name the agent keeps the context under. Defaults to the
                snake_case class name; an explicit name is snake_cased too.
            locks: Lock names required while a parameter of this class is held.

        Returns:
            The declaration recorded.
        """
        declaration = ContextDeclaration(
            name=inflection.underscore(name or cls.__name__),
            locks=tuple(locks or ()),
        )
        self.contexts[cls] = declaration
        return declaration

    def context(
        self,
        *cls: type[T],
        name: str | None = None,
        locks: list[str] | None = None,
    ) -> "type[T] | Callable[[type[T]], type[T]]":
        """Declare a context class: ``@app.context`` or ``@app.context(locks=[...])``.

        A context is an object an agent holds for its lifetime, made by a startup
        hook and handed to actions and hooks by annotation. The class is returned
        unchanged; what this app knows about it is kept here, so one class can be
        a context of any number of apps.

        Args:
            *cls: The class, when used without parentheses.
            name: The name the agent keeps the context under. Defaults to the
                snake_case class name.
            locks: Lock names required while a parameter of this class is held.

        Returns:
            The class, or a class decorator returning it.

        Raises:
            ValueError: If more than one class is passed at once.
        """
        if len(cls) > 1:
            raise ValueError("You can only declare one context at a time.")

        def decorate(target: type[T]) -> type[T]:
            self.register_context(target, name=name, locks=locks)
            return target

        if cls:
            return decorate(cls[0])
        return decorate

    @dataclass_transform(field_specifiers=(model_field,))
    def model(
        self,
        *cls: type[T],
        identifier: str | None = None,
        description: str | None = None,
    ) -> "type[T] | Callable[[type[T]], type[T]]":
        """Declare a model: a class whose instances travel by value, field by field.

        ``@app.model`` or ``@app.model(identifier="@myapp/config")``. The class is made
        a dataclass if it is not one and registered here; a port annotated with
        it is a ``MODEL`` port with one child per field, and a model used inside
        another model has to be declared too.

        Args:
            *cls: The class, when used without parentheses.
            identifier: What it travels as, ``@package/key``. Defaults to
                ``@<the class's module>/<snake_case class name>``.
            description: What it is, for the UI. Defaults to the class docstring.

        Returns:
            The class (a dataclass), or a class decorator returning it.

        Raises:
            ValueError: If more than one class is passed at once.
            TypeError: If the class cannot be made a dataclass.
        """
        if len(cls) > 1:
            raise ValueError("You can only declare one model at a time.")

        def decorate(target: type[T]) -> type[T]:
            target = ensure_model_dataclass(target)
            self.register_as_model(
                target,
                identifier or model_identifier(target.__module__, target),
                description=description or target.__doc__,
            )
            return target

        if cls:
            return decorate(cls[0])
        return decorate

    def model_for(self, cls: Any) -> FullFilledModel | None:
        """The model this app declared for ``cls``, or ``None``."""
        try:
            found = self.find_for_cls(cls)
        except TypeError:  # unhashable annotation
            return None
        return found if isinstance(found, FullFilledModel) else None

    def is_context(self, annotation: Any) -> bool:
        """Whether ``annotation`` is a class this app declared as a context."""
        try:
            return annotation in self.contexts
        except TypeError:  # unhashable annotation
            return False

    def app_context(
        self, *cls: type[T], name: str | None = None
    ) -> "type[T] | Callable[[type[T]], type[T]]":
        """Declare the class of the app context: ``@app.app_context``.

        The app context is the object the caller hands to ``run(context=...)``,
        unrelated to the app's own clients. A hook annotated with the class is
        handed it. The class is returned unchanged.

        Args:
            *cls: The class, when used without parentheses.
            name: What the app context is called. Defaults to the class name.

        Returns:
            The class, or a class decorator returning it.

        Raises:
            ValueError: If more than one class is passed at once.
        """
        if len(cls) > 1:
            raise ValueError("You can only declare one app context at a time.")

        def decorate(target: type[T]) -> type[T]:
            self._refuse_second_app_context(target)
            self.app_contexts[target] = name or target.__name__
            return target

        if cls:
            return decorate(cls[0])
        return decorate

    def is_app_context(self, annotation: Any) -> bool:
        """Whether ``annotation`` is a class this app declared as its app context."""
        try:
            return annotation in self.app_contexts
        except TypeError:  # unhashable annotation
            return False

    @property
    def app_context_class(self) -> type[Any] | None:
        """The one class this app declared as its app context, or ``None``."""
        return next(iter(self.app_contexts), None)

    def _refuse_second_app_context(self, cls: type[Any]) -> None:
        declared = self.app_context_class
        if declared is not None and declared is not cls:
            raise ValueError(
                f"This app already declares {declared.__name__} as its app context; "
                f"an app has one, so {cls.__name__} cannot be another."
            )

    def context_for(self, cls: Any) -> ContextDeclaration:
        """What this app declared about the context class ``cls``.

        Raises:
            KeyError: If ``cls`` is not a context of this app.
        """
        try:
            return self.contexts[cls]
        except (KeyError, TypeError):
            shown = _display_name(cls)
            raise KeyError(
                f"{shown} is not a context this app declared. Declare it with "
                "`@app.context` before naming it."
            ) from None

    def structure(
        self,
        identifier: str,
        *,
        cls: type[object] | None = None,
        widget: AssignWidgetInput | None = None,
        description: str | None = None,
        expand_many: ManyExpander | None = None,
        shrink: Shrinker | None = None,
        returnwidget: ReturnWidgetInput | None = None,
    ) -> Callable[[ExpanderT], ExpanderT]:
        """Declare a type that travels by id: ``@registry.structure("@mikro/image")``.

        The decorated function says how to fetch one back, and names the clients
        it needs the same way an action does. What it returns is the class that
        travels::

            @registry.structure("@mikro/arraydataset")
            async def expand_arraydataset(id: str, mikro: Mikro) -> ArrayDataset:
                return await mikro.aget_array_dataset(id)

        Fetching *many* is not declared here unless it differs: when the injected
        client offers a batch call, binding routes through it.

        Args:
            identifier: What it travels as, ``@package/key``. A contract with
                every app that handles this type.
            cls: The class that travels. Read off the expander's return
                annotation when not given; needed when that is not a class
                (``Any``, a union).
            widget: The widget a user picks one with.
            description: What it is, for the UI.
            expand_many: Fetches several ids at once, when the client's own batch
                call is not the right answer.
            shrink: Turns the object into its id. Defaults to reading ``.id``.
                Like the expander, it may ask for clients by annotation after
                the object (``shrink(dataset, mikro: Mikro)``).
            returnwidget: The widget one is shown with.

        Returns:
            A decorator returning the expander unchanged, so it stays callable
            and keeps its declared type.

        Raises:
            StructureDefinitionError: If the identifier is malformed, the class
                is neither given nor the expander's return annotation, or the
                expander is a lambda or asks for something that is not a client.
        """

        def declare(expand: ExpanderT) -> ExpanderT:
            self.register_as_structure(
                _travelling_class(expand, identifier, cls),
                identifier=identifier,
                aexpand=expand,
                ashrink=shrink,
                aexpand_many=expand_many,
                description=description,
                default_widget=widget,
                default_returnwidget=returnwidget,
            )
            return expand

        return declare

    def is_client(self, annotation: object) -> bool:
        """Whether a parameter annotated ``annotation`` asks for a service's client.

        ``Optional[Mikro]`` and ``Annotated[Mikro, ...]`` still ask for it. True
        when a registered service returns that class (or a subclass of it).

        Args:
            annotation: A parameter annotation.

        Returns:
            Whether a run hands such a parameter a client.
        """
        cls = unwrap_injectable(annotation)
        if not isinstance(cls, type):
            return False
        if getattr(cls, "_is_protocol", False) and not getattr(
            cls, "_is_runtime_protocol", False
        ):
            # A plain ``typing.Protocol`` (a declared dependency, typically) cannot
            # be an ``issubclass`` target, and no service returns one.
            return False
        try:
            return any(issubclass(returned, cls) for returned in self.clients.values())
        except TypeError:
            # Neither can a runtime-checkable one with data members (``Task``'s
            # ``id``): it is matched by isinstance only, and is no client either.
            return False

    def copy_maps(self) -> "StructureRegistry":
        """Copy this registry's maps, sharing their entries.

        Writing to the copy -- binding a client, deriving an enum -- never reaches
        this registry, and the entries themselves are shared until replaced.

        Returns:
            A registry holding the same entries in maps of its own.
        """
        return StructureRegistry(
            identifier_structure_map=dict(self.identifier_structure_map),
            identifier_enum_map=dict(self.identifier_enum_map),
            identifier_memory_structure_map=dict(self.identifier_memory_structure_map),
            identifier_model_map=dict(self.identifier_model_map),
            cls_fullfilled_type_map=dict(self.cls_fullfilled_type_map),
            clients=dict(self.clients),
            protocols=dict(self.protocols),
            states=dict(self.states),
            contexts=dict(self.contexts),
            app_contexts=dict(self.app_contexts),
        )

    def bound(self, app: "BoundApp | Mapping[str, Any]") -> "StructureRegistry":
        """Copy this registry, with its structures bound to ``app``'s clients.

        This registry is left as it was, so one declaration can be bound for any
        number of runs. See :meth:`bind`.

        Args:
            app: The run, or a bare ``{service: client}`` mapping when binding a
                registry on its own.

        Returns:
            The bound copy.

        Raises:
            StructureClientError: If a declared structure's service has no client,
                its client cannot expand structures, or a hand-registered
                structure's expander asks for a client the run has not got.
        """
        return self.copy_maps().bind(app)

    def bind(self, app: "BoundApp | Mapping[str, Any]") -> "StructureRegistry":
        """Hand every structure what it needs from ``app``, in place.

        Two kinds of binding, because there are two kinds of structure:

        * a **declared** one is expanded by its *service's* client, found by the
          service name recorded on it (``app.clients``);
        * a **hand-registered** one carries its own expander, which may ask for
          clients by annotation (``expand(id, mikro: Mikro)``). Those are
          resolved by class, the same way an action's are, and applied to the
          function now -- so nothing is looked up per expansion and
          :class:`~rekuest.structures.types.Expander` never widens.

        Only for a registry that one run owns -- a :meth:`copy_maps` copy, such as
        a snapshot's. The bound entries are copies, so a declaration sharing the
        original entries never sees the client.

        Args:
            app: The run, or a bare ``{service: client}`` mapping when binding a
                registry on its own.

        Returns:
            This registry, bound.

        Raises:
            StructureClientError: If a declared structure's service has no client,
                its client is not a
                :class:`~rekuest.structures.client.StructureClient`, or a
                hand-registered structure's expander wants a client this run has
                not got. Binding fails here, when a run starts, instead of at the
                first expansion inside some action.
        """
        app = _as_bound_app(app)
        for identifier, structure in list(self.identifier_structure_map.items()):
            bound = self._bind_injected(identifier, structure, app)
            if bound is None:
                continue
            self.identifier_structure_map[identifier] = bound
            if self.cls_fullfilled_type_map.get(structure.cls) is structure:
                self.cls_fullfilled_type_map[structure.cls] = bound
        return self

    def _bind_injected(
        self, identifier: str, structure: FullFilledStructure, app: "BoundApp"
    ) -> FullFilledStructure | None:
        """Give a structure the run's clients, and a way to expand many ids.

        Returns ``None`` when there is nothing to do, so a structure that asks for
        no client and cannot batch costs one dict lookup and no copy.
        """
        wanted = structure.injects
        if not wanted:
            return None
        try:
            clients = resolve_service_clients(
                wanted, app, whose=f"the expander of '{identifier}'"
            )
        except StateRequirementsNotMet as e:
            # Reached only if validation was skipped: `AppRegistry.validate` says
            # the same thing earlier, without a connection.
            raise StructureClientError(str(e)) from e

        # One copy, every function at once.
        applied: dict[str, Any] = {
            name: partial(function, **clients)
            for name, function in (
                ("aexpand", structure.aexpand),
                ("aexpand_many", structure.aexpand_many),
                ("ashrink", structure.ashrink),
            )
            if function is not None
        }

        if structure.aexpand_many is None:
            batch = self._batch_through(identifier, clients)
            if batch is not None:
                applied["aexpand_many"] = batch

        return structure.model_copy(update=applied)

    @staticmethod
    def _batch_through(
        identifier: str, clients: Mapping[str, Any]
    ) -> "ManyExpander | None":
        """Borrow a batch expander from an injected client that offers one.

        A structure says how to fetch *one* id, because that is what every client
        API has. Fetching many is the client's business -- mikro answers N ids in
        one federated ``_entities`` request -- so when exactly one injected client
        can do it, expansion goes through that instead of N requests.

        A client may also batch only *some* identifiers, which it says through an
        optional ``can_expand_many(identifier)``.

        Ambiguous on purpose: with two capable clients there is no way to know
        which owns the structure, so neither is used and it falls back to one
        request per id.
        """
        capable = []
        for client in clients.values():
            batch = getattr(client, "aexpand_many", None)
            if not callable(batch):
                continue
            # A client may be able to batch some identifiers and not others --
            # mikro only federates the ones it holds a fragment for, and only
            # against a server that scopes `_entities`. Asking is how it says so.
            asks = getattr(client, "can_expand_many", None)
            if callable(asks) and not asks(identifier):
                continue
            capable.append(batch)
        if len(capable) != 1:
            return None
        return cast("ManyExpander", partial(capable[0], identifier))

    def structures(self) -> list[FullFilledStructure]:
        """The structures registered in this registry, by identifier."""
        return [
            self.identifier_structure_map[identifier]
            for identifier in sorted(self.identifier_structure_map)
        ]

    def get_fullfilled_type_for_cls(self, cls: type[Any]) -> FullFilledType:
        """Get the fullfilled structure for a given class.
        This will use the structure registry to find the correct
        structure for the given class.

        """
        found = self.find_for_cls(cls)
        if found is not None:
            return found

        # Enums and Literals travel by value and are read straight off the
        # annotation, so they are derived rather than registered. Everything
        # else has to have been registered ahead of time.
        if is_literal(cls) or (inspect.isclass(cls) and issubclass(cls, Enum)):
            return self.derive_enum(cls)

        self._refuse_unregistered(cls)

    def fullfill_registration(self, fullfilled_type: FullFilledType) -> None:
        """Record a fullfilled type under its class and its identifier.

        A structure identifier is a wire contract, so two different classes may
        not claim the same one: the second would silently take over every port
        of the first. The *same* class may be recorded again (a declaration
        repeated, or a module re-imported on reload, which makes a new class
        object of the same name), and replaces the entry.

        Raises:
            StructureOverwriteError: If another class already holds the identifier.
        """
        identifier_map = getattr(self, _IDENTIFIER_MAP_FOR[type(fullfilled_type)])
        if isinstance(fullfilled_type, FullFilledStructure):
            existing = identifier_map.get(fullfilled_type.identifier)
            if existing is not None and not _same_class(
                existing.cls, fullfilled_type.cls
            ):
                raise StructureOverwriteError(
                    f"'{fullfilled_type.identifier}' is already registered for "
                    f"{_qualified(existing.cls)}"
                    f"{_declared_by(existing)}, and {_qualified(fullfilled_type.cls)}"
                    f"{_declared_by(fullfilled_type)} claims it too. An identifier "
                    "names one type."
                )
        self.cls_fullfilled_type_map[fullfilled_type.cls] = fullfilled_type
        identifier_map[fullfilled_type.identifier] = fullfilled_type

    def get_port_for_cls(
        self,
        cls: type[Any],
        key: str,
        direction: Literal["arg", "return"],
        nullable: bool = False,
        description: str | None = None,
        effects: list[EffectInput] | None = None,
        label: str | None = None,
        validators: list[ValidatorInput] | None = None,
        default: Any = None,
        assign_widget: AssignWidgetInput | None = None,
        return_widget: ReturnWidgetInput | None = None,
        requires: list[RequiresInput] | None = None,
        provides: list[ProvidesInput] | None = None,
    ) -> ArgPortInput | ReturnPortInput:
        """Create an arg or return port for a registered (or derived enum) class.

        The port kind follows the fullfilled type; enums additionally carry
        their choices and a converted default, and arg structure ports fall
        back to the structure's default widget.
        """
        fullfilled_type = self.get_fullfilled_type_for_cls(cls)
        kind = _PORT_KIND_FOR.get(type(fullfilled_type))
        if kind is None:
            raise StructureRegistryError(
                f"Could not create port for {cls}: unknown fullfilled type {type(fullfilled_type)}"
            )

        is_arg = direction == "arg"
        widget: Any = assign_widget if is_arg else return_widget
        fields: dict[str, Any] = dict(
            kind=kind,
            identifier=fullfilled_type.identifier,
            widget=widget,
            key=key,
            label=label,
            nullable=nullable,
            effects=tuple(effects or []),
            description=description or fullfilled_type.description,
        )
        # Return ports carry neither a default nor validators on the server.
        if is_arg:
            fields["requires"] = tuple(requires) if requires else None
            fields["default"] = None
            fields["validators"] = tuple(validators or [])
        else:
            fields["provides"] = tuple(provides) if provides else None

        if isinstance(fullfilled_type, FullFilledEnum):
            fields["choices"] = tuple(fullfilled_type.choices)
            if is_arg:
                fields["default"] = (
                    fullfilled_type.convert_default(default)
                    if default is not None
                    else None
                )
        elif isinstance(fullfilled_type, FullFilledStructure) and is_arg:
            fields["widget"] = assign_widget or fullfilled_type.default_widget

        port_cls = ArgPortInput if is_arg else ReturnPortInput
        return port_cls(**fields)

    def get_argport_for_cls(
        self,
        cls: type[Any],
        key: str,
        **kwargs: Any,
    ) -> ArgPortInput:
        """Create an :class:`ArgPortInput` for a class (see :meth:`get_port_for_cls`)."""
        return cast(ArgPortInput, self.get_port_for_cls(cls, key, "arg", **kwargs))

    def get_returnport_for_cls(
        self,
        cls: type[Any],
        key: str,
        **kwargs: Any,
    ) -> ReturnPortInput:
        """Create a :class:`ReturnPortInput` for a class (see :meth:`get_port_for_cls`)."""
        return cast(
            ReturnPortInput, self.get_port_for_cls(cls, key, "return", **kwargs)
        )
