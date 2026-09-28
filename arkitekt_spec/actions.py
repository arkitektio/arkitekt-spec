"""The action language: how an app describes its actions, states, locks and bloks.

Owned by the spec. This module was seeded on 2026-09-28 from rekuest's generated
protocol inputs (``rekuest/protocol/schema.py``), with the rekuest-specific traits
left out, and is hand-maintained from then on -- it is NOT regenerated. rekuest
(``rekuest.protocol``), the kabinet client and the servers take these types from here.

Unlike the envelope models, these refuse unknown keys (``extra='forbid'``): a typo in
a port must fail where it is written. Consumers therefore upgrade the spec before
producers start emitting a new field.
"""

import hashlib
import json
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from arkitekt_spec.scalars import (
    JSONSerializable,
    SearchQuery,
)


class GraphQLDefault:
    """Records a GraphQL field schema default value. The client omits the field so the server applies its own default; this preserves the value for introspection."""

    def __init__(self, value: str) -> None:
        """Record the default, as the schema spells it."""
        self.value = value

    def __repr__(self) -> str:
        """Show the recorded default."""
        return "GraphQLDefault(" + repr(self.value) + ")"


class ActionModel(BaseModel):
    """The base of every action-language model: an explicit null means "the default".

    GraphQL clients omit a field to get its server-side default, and older producers
    wrote that omission as an explicit ``null`` (e.g. ``pure: null``). The defaults
    live on the models here, so a ``null`` for a field whose default is not ``None``
    is dropped before validation and the default applies -- the same value the server
    would have filled in, which is also what keeps :func:`definition_hash` identical
    on both sides.
    """

    @model_validator(mode="before")
    @classmethod
    def _null_means_default(cls, data: Any) -> Any:  # noqa: ANN401
        if not isinstance(data, dict):
            return data
        dropped: dict[str, Any] = {}
        items: dict[str, Any] = data  # pyright: ignore[reportUnknownVariableType]
        for key, value in items.items():
            if value is None:
                field = cls.model_fields.get(key) or next(
                    (
                        f
                        for f in cls.model_fields.values()
                        if f.validation_alias is not None
                        and key in getattr(f.validation_alias, "choices", ())
                    ),
                    None,
                )
                if (
                    field is not None
                    and not field.is_required()
                    and field.get_default(call_default_factory=True) is not None
                ):
                    continue
            dropped[key] = value
        return dropped


class ActionKind(str, Enum):
    """The kind of action."""

    FUNCTION = "FUNCTION"
    GENERATOR = "GENERATOR"
    __str__ = str.__str__


class AssignPolicy(str, Enum):
    """No documentation"""

    AUTOMATIC = "AUTOMATIC"
    BALANCED = "BALANCED"
    ROUND_ROBIN = "ROUND_ROBIN"
    LEAST_BUSY = "LEAST_BUSY"
    FASTEST_RESPONSE = "FASTEST_RESPONSE"
    __str__ = str.__str__


class DescriptorOperator(str, Enum):
    """The operator of a requires/provides descriptor: how a port's constraint compares the object's value at `key` with `value`."""

    MATCHES = "MATCHES"
    EXISTS = "EXISTS"
    LTE = "LTE"
    GTE = "GTE"
    EQUALS = "EQUALS"
    CONTAINS = "CONTAINS"
    NOT_EQUALS = "NOT_EQUALS"
    IN = "IN"
    NOT_IN = "NOT_IN"
    __str__ = str.__str__


class EffectClass(str, Enum):
    """The effect class of an implementation — declared by the implementation, never the caller. NONE work is freely retryable/reclaimable; PHYSICAL work touches the real world (no UPSERT), so an ambiguous failure is terminal and must not be retried."""

    NONE = "NONE"
    PHYSICAL = "PHYSICAL"
    __str__ = str.__str__


class EffectKind(str, Enum):
    """The kind of effect."""

    MESSAGE = "MESSAGE"
    HIDE = "HIDE"
    CUSTOM = "CUSTOM"
    __str__ = str.__str__


class OptionKey(str, Enum):
    """No documentation"""

    LABEL = "LABEL"
    DESCRIPTION = "DESCRIPTION"
    LOGO = "LOGO"
    VALUE = "VALUE"
    __str__ = str.__str__


class PortKind(str, Enum):
    """The kind of a port: its structural type. Decides which of children, identifier and choices the port must, may or must not carry (see docs/design/ports.md)."""

    INT = "INT"
    "An integer. No children; choices optional."
    STRING = "STRING"
    "A string. No children; choices optional."
    STRUCTURE = "STRUCTURE"
    "A reference to an object held by a service, typed by `identifier` (@package/key, required). Values are ids. No children."
    LIST = "LIST"
    "A list; exactly one child describes the item type (conventionally keyed '...')."
    BOOL = "BOOL"
    "A boolean. No children."
    DICT = "DICT"
    "A string-keyed map. One child keyed '...' describes a homogeneous value type; several named children describe the known keys."
    FLOAT = "FLOAT"
    "A floating point number. No children; choices optional."
    DATE = "DATE"
    "An ISO-8601 date or datetime string. No children."
    UNION = "UNION"
    "One of several variants; at least two children, each a variant."
    ENUM = "ENUM"
    "One of a fixed set of values; `choices` required."
    MODEL = "MODEL"
    "An object with named fields; at least one child per field, `identifier` optional."
    MEMORY_STRUCTURE = "MEMORY_STRUCTURE"
    "A reference to an object that lives in the agent's memory, typed by `identifier` (required). Makes the action LOCAL-scoped. No children."
    INTERFACE = "INTERFACE"
    "A reference to any object implementing an interface, typed by `identifier` (required). No children."
    QUANTITY = "QUANTITY"
    "A physical quantity with a unit; `reference_unit` required, `dimension` derived. No children."
    __str__ = str.__str__


class WindowFunction(str, Enum):
    """Aggregation computed over a tracked value within a window."""

    MEAN = "MEAN"
    MIN = "MIN"
    MAX = "MAX"
    SUM = "SUM"
    COUNT = "COUNT"
    LAST = "LAST"
    FIRST = "FIRST"
    STD = "STD"
    __str__ = str.__str__


class ChoiceAssignWidgetInput(ActionModel):
    """A dropdown over the port's `choices`."""

    kind: Literal["CHOICE"] = Field(default="CHOICE")
    follow_value: str | None = Field(
        validation_alias=AliasChoices("follow_value", "followValue"),
        serialization_alias="followValue",
        default=None,
        description="Port path of another port whose value this widget follows and mirrors.",
    )
    placeholder: str | None = Field(
        default=None,
        description="The placeholder text shown before a choice is made. The choices themselves are the port's `choices`.",
    )

    def model_post_init(self, context: Any, /) -> None:  # noqa: ANN401, D102
        self.__pydantic_fields_set__.update({"kind"})

    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, use_enum_values=True
    )


class ChoiceReturnWidgetInput(ActionModel):
    """Displays the port's `choices` label for a returned value."""

    kind: Literal["CHOICE"] = Field(default="CHOICE")

    def model_post_init(self, context: Any, /) -> None:  # noqa: ANN401, D102
        self.__pydantic_fields_set__.update({"kind"})

    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, use_enum_values=True
    )


class CustomAssignWidgetInput(ActionModel):
    """A catalog component rendered as the port's widget."""

    kind: Literal["CUSTOM"] = Field(default="CUSTOM")
    follow_value: str | None = Field(
        validation_alias=AliasChoices("follow_value", "followValue"),
        serialization_alias="followValue",
        default=None,
        description="Port path of another port whose value this widget follows and mirrors.",
    )
    component: str = Field(
        description="The catalog component to render. The port value is in scope as the reserved root `value`."
    )
    props: tuple["ComponentPropInput", ...] | None = Field(
        default=None,
        description="Props of the component. value_paths may only reference `value` and `dependencies`; agent calls are not allowed.",
    )
    dependencies: Annotated[tuple[str, ...], GraphQLDefault("[]")] = Field(
        default=(),
        description="The other ports (port paths, `..` traverses children) whose values the props may reference.",
    )
    "The other ports (port paths, `..` traverses children) whose values the props may reference.\nDefault: []"
    fallback: "AssignWidgetInput | None" = Field(
        default=None,
        description="Widget to render when the UI has no such component in its catalog.",
    )

    def model_post_init(self, context: Any, /) -> None:  # noqa: ANN401, D102
        self.__pydantic_fields_set__.update({"kind"})

    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, use_enum_values=True
    )


class CustomReturnWidgetInput(ActionModel):
    """A catalog component rendered for a returned value."""

    kind: Literal["CUSTOM"] = Field(default="CUSTOM")
    component: str = Field(
        description="The catalog component to render. The returned value is in scope as the reserved root `value`."
    )
    props: tuple["ComponentPropInput", ...] | None = Field(
        default=None,
        description="Props of the component; value_paths may only reference `value`, agent calls are not allowed.",
    )

    def model_post_init(self, context: Any, /) -> None:  # noqa: ANN401, D102
        self.__pydantic_fields_set__.update({"kind"})

    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, use_enum_values=True
    )


class ProxyAssignWidgetInput(ActionModel):
    """Delegates the port to a port of another action."""

    kind: Literal["PROXY"] = Field(default="PROXY")
    follow_value: str | None = Field(
        validation_alias=AliasChoices("follow_value", "followValue"),
        serialization_alias="followValue",
        default=None,
        description="Port path of another port whose value this widget follows and mirrors.",
    )
    target_port: str = Field(
        validation_alias=AliasChoices("target_port", "targetPort"),
        serialization_alias="targetPort",
        description="The port key on the targeted action.",
    )
    target_action: str = Field(
        validation_alias=AliasChoices("target_action", "targetAction"),
        serialization_alias="targetAction",
        description="The action to target: an action-dependency key of `target_dependency` when that is set.",
    )
    target_dependency: str | None = Field(
        validation_alias=AliasChoices("target_dependency", "targetDependency"),
        serialization_alias="targetDependency",
        default=None,
        description="The agent dependency (by key) that provides the targeted action; omitted: the implementing agent itself.",
    )

    def model_post_init(self, context: Any, /) -> None:  # noqa: ANN401, D102
        self.__pydantic_fields_set__.update({"kind"})

    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, use_enum_values=True
    )


class SearchAssignWidgetInput(ActionModel):
    """A search over a ward for STRUCTURE ports (or lists of them)."""

    kind: Literal["SEARCH"] = Field(default="SEARCH")
    follow_value: str | None = Field(
        validation_alias=AliasChoices("follow_value", "followValue"),
        serialization_alias="followValue",
        default=None,
        description="Port path of another port whose value this widget follows and mirrors.",
    )
    query: SearchQuery = Field(
        description="The GraphQL query the ward executes to populate the choices. Must be a single `query` operation declaring `$search: String` and `$values: [ID!]`, plus one variable per filter port key."
    )
    ward: str = Field(description="The ward (service) that executes the query.")
    filters: tuple["ArgPortInput", ...] | None = Field(
        default=None,
        description="Filter ports whose values are passed to the query as variables named by their keys.",
    )
    dependencies: Annotated[tuple[str, ...], GraphQLDefault("[]")] = Field(
        default=(),
        description="The other ports (port paths, `..` traverses children) whose values the query may reference.",
    )
    "The other ports (port paths, `..` traverses children) whose values the query may reference.\nDefault: []"
    placeholder: str | None = Field(default=None, description="The placeholder text.")

    def model_post_init(self, context: Any, /) -> None:  # noqa: ANN401, D102
        self.__pydantic_fields_set__.update({"kind"})

    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, use_enum_values=True
    )


class SliderAssignWidgetInput(ActionModel):
    """A numeric slider for INT, FLOAT and QUANTITY ports."""

    kind: Literal["SLIDER"] = Field(default="SLIDER")
    follow_value: str | None = Field(
        validation_alias=AliasChoices("follow_value", "followValue"),
        serialization_alias="followValue",
        default=None,
        description="Port path of another port whose value this widget follows and mirrors.",
    )
    min: float | None = Field(default=None, description="The minimum value.")
    max: float | None = Field(default=None, description="The maximum value.")
    step: float | None = Field(
        default=None, description="The step between selectable values; must be positive."
    )

    def model_post_init(self, context: Any, /) -> None:  # noqa: ANN401, D102
        self.__pydantic_fields_set__.update({"kind"})

    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, use_enum_values=True
    )


class StateChoiceAssignWidgetInput(ActionModel):
    """A choice over entries of an agent's state."""

    kind: Literal["STATE_CHOICE"] = Field(default="STATE_CHOICE")
    follow_value: str | None = Field(
        validation_alias=AliasChoices("follow_value", "followValue"),
        serialization_alias="followValue",
        default=None,
        description="Port path of another port whose value this widget follows and mirrors.",
    )
    dependency: str | None = Field(
        default=None,
        description="The agent dependency (by key) whose state provides the choices; omitted: the implementing agent's own state.",
    )
    state_path: str | None = Field(
        validation_alias=AliasChoices("state_path", "statePath"),
        serialization_alias="statePath",
        default=None,
        description="Static JSON pointer into the state value that provides the choices. Mutually exclusive with `state_call`.",
    )
    state_call: "UtilCallInput | None" = Field(
        validation_alias=AliasChoices("state_call", "stateCall"),
        serialization_alias="stateCall",
        default=None,
        description="Pure UtilCall returning that pointer dynamically; may reference `state`, `value` and `dependencies`. Mutually exclusive with `state_path`.",
    )
    state_accessors: tuple["StateAccessorInput", ...] | None = Field(
        validation_alias=AliasChoices("state_accessors", "stateAccessors"),
        serialization_alias="stateAccessors",
        default=None,
        description="How to read label/description/logo/value out of each state entry; each accessor is a static pointer or a pure call.",
    )
    dependencies: Annotated[tuple[str, ...], GraphQLDefault("[]")] = Field(
        default=(),
        description="The other ports (port paths, `..` traverses children) whose values the calls may reference.",
    )
    "The other ports (port paths, `..` traverses children) whose values the calls may reference.\nDefault: []"

    def model_post_init(self, context: Any, /) -> None:  # noqa: ANN401, D102
        self.__pydantic_fields_set__.update({"kind"})

    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, use_enum_values=True
    )


class StringAssignWidgetInput(ActionModel):
    """A text input for STRING ports."""

    kind: Literal["STRING"] = Field(default="STRING")
    follow_value: str | None = Field(
        validation_alias=AliasChoices("follow_value", "followValue"),
        serialization_alias="followValue",
        default=None,
        description="Port path of another port whose value this widget follows and mirrors.",
    )
    placeholder: str | None = Field(default=None, description="The placeholder text.")
    as_paragraph: bool | None = Field(
        validation_alias=AliasChoices("as_paragraph", "asParagraph"),
        serialization_alias="asParagraph",
        default=None,
        description="Render as a multi-line paragraph.",
    )

    def model_post_init(self, context: Any, /) -> None:  # noqa: ANN401, D102
        self.__pydantic_fields_set__.update({"kind"})

    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, use_enum_values=True
    )


class ActionArgumentInput(ActionModel):
    """A JSON-serializable argument entry for a multi-agent action trigger."""

    key: str | None = Field(default=None, description="The argument property name.")
    value_literal: JSONSerializable | None = Field(
        validation_alias=AliasChoices("value_literal", "valueLiteral"),
        serialization_alias="valueLiteral",
        default=None,
        description="Static literal value if not dynamically bound.",
    )
    value_path: str | None = Field(
        validation_alias=AliasChoices("value_path", "valuePath"),
        serialization_alias="valuePath",
        default=None,
        description="JSON Pointer referencing the shared Blok state to inject into this argument slot dynamically.",
    )
    agent_call: "AgentProbeInput | None" = Field(
        validation_alias=AliasChoices("agent_call", "agentCall"),
        serialization_alias="agentCall",
        default=None,
        description="Defines a nested agent call if this argument should trigger an agent interaction.",
    )
    util_call: "UtilCallInput | None" = Field(
        validation_alias=AliasChoices("util_call", "utilCall"),
        serialization_alias="utilCall",
        default=None,
        description="Defines a nested utility call if this argument should trigger a system utility interaction.",
    )
    value_list: tuple["ActionArgumentInput", ...] | None = Field(
        validation_alias=AliasChoices("value_list", "valueList"),
        serialization_alias="valueList",
        default=None,
        description="Defines a list of values if this argument should be an array.",
    )
    value_dict: tuple["ActionArgumentInput", ...] | None = Field(
        validation_alias=AliasChoices("value_dict", "valueDict"),
        serialization_alias="valueDict",
        default=None,
        description="Defines a list of key-value pairs if this argument should be a dictionary.",
    )
    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, use_enum_values=True
    )


class ActionDemandInput(ActionModel):
    """Pure matching criteria for an action: hash or name short-circuits, arg/return
    port matches, protocols and forced port counts. Used directly by query filters and, wrapped
    in an ActionDependencyInput, by dependency declarations."""

    hash: str | None = Field(
        default=None,
        description="The exact hash of the action. When set, matching short-circuits on the hash and everything else is ignored.",
    )
    key: str | None = Field(
        default=None,
        description="The action's key within its app, e.g. 'open_image'. Together with `app` this is the preferred identification of the demanded action.",
    )
    app: str | None = Field(
        default=None,
        description="The identifier of the app providing the action, e.g. 'imagej'. Omit (or drop when loosening) to allow equivalent actions from any app.",
    )
    version: str | None = Field(default=None, description="The exact version of the action.")
    name: str | None = Field(default=None, description="The display name of the action to match.")
    arg_matches: tuple["PortMatchInput", ...] | None = Field(
        validation_alias=AliasChoices("arg_matches", "argMatches"),
        serialization_alias="argMatches",
        default=None,
        description="The matches the action's arg ports must satisfy.",
    )
    return_matches: tuple["PortMatchInput", ...] | None = Field(
        validation_alias=AliasChoices("return_matches", "returnMatches"),
        serialization_alias="returnMatches",
        default=None,
        description="The matches the action's return ports must satisfy.",
    )
    protocols: tuple[str, ...] | None = Field(
        default=None, description="Protocols (by name) the action must implement, e.g. 'predicate'."
    )
    force_arg_length: int | None = Field(
        validation_alias=AliasChoices("force_arg_length", "forceArgLength"),
        serialization_alias="forceArgLength",
        default=None,
        description="Require that the action has exactly this number of root args.",
    )
    force_return_length: int | None = Field(
        validation_alias=AliasChoices("force_return_length", "forceReturnLength"),
        serialization_alias="forceReturnLength",
        default=None,
        description="Require that the action has exactly this number of root returns.",
    )
    pure: bool | None = Field(
        default=None, description="Require the action to be (or not be) pure. Omit to match either."
    )
    idempotent: bool | None = Field(
        default=None,
        description="Require the action to be (or not be) idempotent. Omit to match either.",
    )
    stateful: bool | None = Field(
        default=None,
        description="Require the action to be (or not be) stateful. Omit to match either.",
    )
    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, use_enum_values=True
    )


class ActionDependencyInput(ActionModel):
    """A named action requirement of a dependency: a slot key plus the demand the
    resolved action must satisfy, and resolution-lifecycle filters."""

    key: str = Field(
        description="The local slot key of this action requirement — callers reference it when assigning."
    )
    description: str | None = Field(
        default=None,
        description="The description of the dependency, why it is needed and what it is used for.",
    )
    demand: ActionDemandInput | None = Field(
        default=None,
        description="The matching criteria the resolved action must satisfy (app/key preferred; matches loosen).",
    )
    optional: Annotated[bool, GraphQLDefault("False")] = Field(
        default=False,
        description="Whether the dependency is optional or not. If the dependency is optional, the agent doesn't have to provide it to be potentially callable",
    )
    "Whether the dependency is optional or not. If the dependency is optional, the agent doesn't have to provide it to be potentially callable\nDefault: False"
    allow_inactive: Annotated[bool, GraphQLDefault("True")] = Field(
        validation_alias=AliasChoices("allow_inactive", "allowInactive"),
        serialization_alias="allowInactive",
        default=True,
        description="Allow inactive nodes, defaults to true",
    )
    "Allow inactive nodes, defaults to true\nDefault: True"
    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, use_enum_values=True
    )


class AgentDependencyInput(ActionModel):
    """A dependency for a implementation. By defining dependencies, you can
    create a dependency graph for your implementations and actions"""

    key: str = Field(
        description="The key of this dependency, when assigning you can reference this key to specify which agent_dependency you are assigning to."
    )
    app: str | None = Field(
        default=None,
        description="Which app this dependency corresponds to (i.e. do you want to use a stardist agent for that or imagej agents needs to be a world unique classsifier (reverse domain notation) that identifies the type of agent you want to use, and then we can have multiple agents of the same type running in the system, e.g. startdist could be the app for all agents that correpsond to a startdist instance)",
    )
    version: str | None = Field(
        default=None, description="The version of the app this dependency corresponds to."
    )
    name: str | None = Field(
        default=None,
        description="The name of the agent. This is used to identify the agent in the system.",
    )
    description: str | None = Field(
        default=None,
        description="A description of the dependency, why it is needed and what it is used for. This can be used to provide more context to users when assigning dependencies.",
    )
    optional: Annotated[bool, GraphQLDefault("False")] = Field(
        default=False,
        description="Whether the dependency is optional or not. If the dependency is optional, users can choose to not provide it",
    )
    "Whether the dependency is optional or not. If the dependency is optional, users can choose to not provide it\nDefault: False"
    action_dependencies: tuple[ActionDependencyInput, ...] | None = Field(
        validation_alias=AliasChoices("action_dependencies", "actionDependencies"),
        serialization_alias="actionDependencies",
        default=None,
        description="The named action requirements of the agent — each a slot key plus the demand the resolved action must satisfy.",
    )
    state_dependencies: tuple["StateDependencyInput", ...] | None = Field(
        validation_alias=AliasChoices("state_dependencies", "stateDependencies"),
        serialization_alias="stateDependencies",
        default=None,
        description="The named state requirements of the agent — each a slot key plus the demand the agent's state must satisfy.",
    )
    auto_resolvable: Annotated[bool, GraphQLDefault("False")] = Field(
        validation_alias=AliasChoices("auto_resolvable", "autoResolvable"),
        serialization_alias="autoResolvable",
        default=False,
        description="Whether this dependency is auto resolvable or not. If so we will try to automatically resolve it based on the demands specified in the dependency and the capabilities of the available agents in the system. This is used to identify the demand in the system. Attention if any of the dependencies of this agent dependency is not auto resolvable, this dependency will also not be auto resolvable",
    )
    "Whether this dependency is auto resolvable or not. If so we will try to automatically resolve it based on the demands specified in the dependency and the capabilities of the available agents in the system. This is used to identify the demand in the system. Attention if any of the dependencies of this agent dependency is not auto resolvable, this dependency will also not be auto resolvable\nDefault: False"
    mutually_exclusive_keys: tuple[str, ...] | None = Field(
        validation_alias=AliasChoices("mutually_exclusive_keys", "mutuallyExclusiveKeys"),
        serialization_alias="mutuallyExclusiveKeys",
        default=None,
        description="A list of keys of other agent dependencies that are mutually exclusive with this one. This means two agent dependencies with mutually exclusive keys cannot be assigned to the same implementing agent. This is used to identify the demand in the system.",
    )
    min_viable_instances: int | None = Field(
        validation_alias=AliasChoices("min_viable_instances", "minViableInstances"),
        serialization_alias="minViableInstances",
        default=None,
        description="The minimum amount of viable instances for the agent. This is used to identify the demand in the system.",
    )
    max_viable_instances: int | None = Field(
        validation_alias=AliasChoices("max_viable_instances", "maxViableInstances"),
        serialization_alias="maxViableInstances",
        default=None,
        description="The maximum amount of viable instances for the agent. This is used to identify the demand in the system.",
    )
    prefered_instances: int | None = Field(
        validation_alias=AliasChoices("prefered_instances", "preferedInstances"),
        serialization_alias="preferedInstances",
        default=None,
        description="The prefered amount of instances for the agent. This is used to identify the demand in the system.",
    )
    assign_policy: Annotated[AssignPolicy, GraphQLDefault("BALANCED")] = Field(
        validation_alias=AliasChoices("assign_policy", "assignPolicy"),
        serialization_alias="assignPolicy",
        default=AssignPolicy.BALANCED,
        description="The policy used to pick which instance of the agent to assign to.",
    )
    "The policy used to pick which instance of the agent to assign to.\nDefault: BALANCED"
    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, use_enum_values=True
    )


class AgentProbeInput(ActionModel):
    """Defines a callback that routes user interactions directly to an Arkitekt Agent via Rekuest."""

    dependency: str = Field(
        description="The abstract agent dependency key declared in the Blok manifest (e.g., 'stage_dep')."
    )
    operation: str = Field(
        description="The target function name registered on that specific agent's worker thread loop."
    )
    arguments: tuple[ActionArgumentInput, ...] | None = Field(
        default=None, description="Key-value arguments map compiled for the target agent call."
    )
    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, use_enum_values=True
    )


class ArgPortInput(ActionModel):
    """A Port is a single input or output of an action, identified by its `key` and typed by its `kind`.

    STRUCTURE, MEMORY_STRUCTURE and INTERFACE ports carry an `identifier` of the form `@package/key`
    (e.g. `@mikro/image`); ports with the same identifier are compatible. LIST and DICT ports have one
    child (the item type), UNION ports two or more (the variants), MODEL ports one per field. ENUM ports
    declare `choices`. See docs/design/ports.md for the full table.
    """

    key: str = Field(
        description="The key of the port: unique among its siblings, free of '..', not 'value'. LIST/DICT item ports are conventionally keyed '...'."
    )
    label: str | None = Field(
        default=None,
        description="The label of the port. This is the text that is displayed in the UI",
    )
    kind: PortKind = Field(
        description="The kind of the port. This is the type of the port. Can be either int, string, structure, list, bool, dict, float, date, union or model"
    )
    description: str | None = Field(
        default=None,
        description="The description of the port. This is the text that is displayed in the UI when the user hovers over the port",
    )
    identifier: str | None = Field(
        default=None,
        description="The identifier of the port's type, of the form @package/key. Required for STRUCTURE, MEMORY_STRUCTURE and INTERFACE, where it is the only identity a value has; optional for MODEL and ENUM, where it names the class or enum the port was built from so that agents can map a value back to it.",
    )
    nullable: Annotated[bool, GraphQLDefault("False")] = Field(
        default=False,
        description="Whether the port is nullable or not. If the port is nullable, it can be set to null. If the port is not nullable, it cannot be set to null",
    )
    "Whether the port is nullable or not. If the port is nullable, it can be set to null. If the port is not nullable, it cannot be set to null\nDefault: False"
    effects: tuple["EffectInput", ...] | None = Field(
        default=None, description="The effects of the port"
    )
    choices: tuple["ChoiceInput", ...] | None = Field(
        default=None,
        description="The values the port accepts (required for ENUM; optional for INT, FLOAT, STRING). Rendered by CHOICE widgets.",
    )
    reference_unit: str | None = Field(
        validation_alias=AliasChoices("reference_unit", "referenceUnit"),
        serialization_alias="referenceUnit",
        default=None,
        description='For QUANTITY ports: the canonical/reference unit of the physical quantity, e.g. "volt" or "farad". It is the default selection and the key used to resolve the concrete quantity type; other units of the same dimension are still allowed.',
    )
    proposed_units: tuple[str, ...] | None = Field(
        validation_alias=AliasChoices("proposed_units", "proposedUnits"),
        serialization_alias="proposedUnits",
        default=None,
        description='For QUANTITY ports: units offered as a dropdown in the UI, e.g. ["pF", "nF", "uF"]. Proposals only — any unit of the same dimension remains valid input.',
    )
    dimension: str | None = Field(
        default=None,
        description='For QUANTITY ports: the pint dimensionality string, e.g. "[mass] * [length] ** 2 / [time] ** 3 / [current]". This is the wiring-compatibility key between quantity ports.',
    )
    children: tuple["ArgPortInput", ...] | None = Field(
        default=None, description="The child ports (used for list, dict, union and model ports)."
    )
    validators: tuple["ValidatorInput", ...] | None = Field(
        default=None, description="The validators for the port"
    )
    default: Any | None = Field(
        default=None, description="The default value for the port; must fit the port's kind."
    )
    widget: "AssignWidgetInput | None" = Field(
        default=None, description="The assign widget to use for this port, discriminated by `kind`."
    )
    requires: tuple["RequiresInput", ...] | None = Field(
        default=None,
        description="The descriptors for the port. Descriptors are key-value pairs that can be used to add additional metadata to a port. When using rekuest's action search, you can filter actions based on their port descriptors",
    )
    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, use_enum_values=True
    )


AssignWidgetInput = Annotated[
    ChoiceAssignWidgetInput
    | CustomAssignWidgetInput
    | ProxyAssignWidgetInput
    | SearchAssignWidgetInput
    | SliderAssignWidgetInput
    | StateChoiceAssignWidgetInput
    | StringAssignWidgetInput,
    Field(discriminator="kind"),
]


class BlokImplementationInput(ActionModel):
    """Which locks does the agent provide in general"""

    key: str = Field(description="The key of this Blok implementation.")
    dependencies: Annotated[tuple[AgentDependencyInput, ...], GraphQLDefault("[]")] = Field(
        default=(), description="The dependencies required by this Blok."
    )
    "The dependencies required by this Blok.\nDefault: []"
    components: tuple["ComponentNodeInput", ...] = Field(
        description="The UI component tree blueprint for this Blok."
    )
    catalog: str | None = Field(
        default=None,
        description="The optional catalog name if this Blok should be registered inside a specific namespace in your Electron app's UI component registry.",
    )
    description: str | None = Field(
        default=None,
        description="A human-readable description about this Blok's purpose and functionality.",
    )
    demo_state: JSONSerializable | None = Field(
        validation_alias=AliasChoices("demo_state", "demoState"),
        serialization_alias="demoState",
        default=None,
        description="An optional JSON-serializable object providing demo state values for this Blok's internal reactive data model, useful for testing and development purposes.",
    )
    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, use_enum_values=True
    )


class ChoiceInput(ActionModel):
    """
    A choice is a value that can be selected in a dropdown.

    It is composed of a value, a label, and a description. The value is the
    value that is returned when the choice is selected. The label is the
    text that is displayed in the dropdown. The description is the text
    that is displayed when the user hovers over the choice.

    """

    value: Any = Field(
        description="The value of the choice (any JSON value); must fit the port's kind. This is the value that is returned when the choice is selected"
    )
    label: str = Field(
        description="The label of the choice. This is the text that is displayed in the UI"
    )
    image: str | None = Field(
        default=None,
        description="The image of the choice. This is the image that is displayed in the UI (must be a URL)",
    )
    description: str | None = Field(
        default=None,
        description="The description of the choice. This is the text that is displayed in the UI when the user hovers over the choice",
    )
    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, use_enum_values=True
    )


class ComponentNodeInput(ActionModel):
    """An abstract structural visual element inside a Blok blueprint manifest."""

    id: str = Field(
        description="Unique structural string identifying this node instance inside the flat workspace layout tree."
    )
    component: str = Field(
        description="The type indicator token matching your Electron app's registered catalog specs (e.g. 'Slider')."
    )
    props: tuple["ComponentPropInput", ...] | None = Field(
        default=None,
        description="The collection of static values, state pointers, or action endpoints assigned to this component.",
    )
    children: tuple["ComponentNodeInput", ...] | None = Field(
        default=None,
        description="Flat adjacency pointer list mapping out IDs nested inside this specific component layer.",
    )
    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, use_enum_values=True
    )


class ComponentPropInput(ActionModel):
    """A single key-value prop configuration for a component layout node."""

    key: str = Field(description="The prop key name matching the target UI catalog constraint.")
    static_value: JSONSerializable | None = Field(
        validation_alias=AliasChoices("static_value", "staticValue"),
        serialization_alias="staticValue",
        default=None,
        description="A raw scalar or JSON-stringified literal configuration parameter (e.g. '40x' or True).",
    )
    dynamic_value: "DynamicValueInput | None" = Field(
        validation_alias=AliasChoices("dynamic_value", "dynamicValue"),
        serialization_alias="dynamicValue",
        default=None,
        description="A reactive state data-binding rule.",
    )
    declares_value: str | None = Field(
        validation_alias=AliasChoices("declares_value", "declaresValue"),
        serialization_alias="declaresValue",
        default=None,
        description="If set, this prop declares a new 'value' in the Blok state that can be referenced by other props or actions. The value of this field should be the name of the declared value (e.g., 'selected_user').",
    )
    agent_call: AgentProbeInput | None = Field(
        validation_alias=AliasChoices("agent_call", "agentCall"),
        serialization_alias="agentCall",
        default=None,
        description="Defines an imperative interactive network action callback loop if this prop should trigger an agent interaction.",
    )
    util_call: "UtilCallInput | None" = Field(
        validation_alias=AliasChoices("util_call", "utilCall"),
        serialization_alias="utilCall",
        default=None,
        description="Defines an imperative interactive network action callback loop if this prop should trigger a system utility interaction.",
    )
    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, use_enum_values=True
    )


class DefinitionInput(ActionModel):
    """A definition

    Definitions are the building implementation for Actions and provide the
    information needed to create a action. They are primarly composed of a name,
    a description, and a list of ports.

    Definitions provide a protocol of input and output, and do not contain
    any information about the actual implementation of the action ( this is handled
    by a implementation that implements a action).

    """

    description: str | None = Field(
        default=None,
        description="The description of the definition. This is the text that is displayed in the UI",
    )
    collections: Annotated[tuple[str, ...], GraphQLDefault("[]")] = Field(
        default=(),
        description="The collections of the definition. This is used to group definitions together in the UI",
    )
    "The collections of the definition. This is used to group definitions together in the UI\nDefault: []"
    key: str = Field(
        description="The key of the definition. This is used to uniquely identify the definition"
    )
    version: str = Field(
        description="The version of the definition. This is used to differentiate if the underyling algorithm has changed, i.e we would expect different results for the same input"
    )
    name: str = Field(
        description="The name of the actions. This is used to uniquely identify the definition"
    )
    stateful: Annotated[bool, GraphQLDefault("False")] = Field(
        default=False,
        description="Whether the definition is stateful or not. If the definition is stateful, it can be used to create a stateful action. If the definition is not stateful, it cannot be used to create a stateful action",
    )
    "Whether the definition is stateful or not. If the definition is stateful, it can be used to create a stateful action. If the definition is not stateful, it cannot be used to create a stateful action\nDefault: False"
    pure: Annotated[bool, GraphQLDefault("False")] = Field(
        default=False,
        description="Whether the action is pure: same args always produce the same result and no side effects — its results are replayable/cacheable. Implies idempotent. Incompatible with stateful and with a PHYSICAL effect class.",
    )
    "Whether the action is pure: same args always produce the same result and no side effects — its results are replayable/cacheable. Implies idempotent. Incompatible with stateful and with a PHYSICAL effect class.\nDefault: False"
    idempotent: Annotated[bool, GraphQLDefault("False")] = Field(
        default=False,
        description="Whether the action is idempotent: safe to run multiple times with the same args without changing the outcome — on ambiguous executor loss it may be freely re-dispatched.",
    )
    "Whether the action is idempotent: safe to run multiple times with the same args without changing the outcome — on ambiguous executor loss it may be freely re-dispatched.\nDefault: False"
    allow_probe: Annotated[bool, GraphQLDefault("False")] = Field(
        validation_alias=AliasChoices("allow_probe", "allowProbe"),
        serialization_alias="allowProbe",
        default=False,
        description="Whether the action may be invoked as a probe: zero persistence, redis-held state, no history/replay/recovery. Only actions declaring this are callable via the call mutation.",
    )
    "Whether the action may be invoked as a probe: zero persistence, redis-held state, no history/replay/recovery. Only actions declaring this are callable via the call mutation.\nDefault: False"
    catalogs: Annotated[tuple[str, ...], GraphQLDefault("[]")] = Field(
        default=(),
        description="Names of the UI catalogs that extend the base catalog (`base@1`, always applied) for this definition's effect and validator calls. Unknown names yield an unknown_catalog warning; conflicting operation definitions across catalogs are a registration error.",
    )
    "Names of the UI catalogs that extend the base catalog (`base@1`, always applied) for this definition's effect and validator calls. Unknown names yield an unknown_catalog warning; conflicting operation definitions across catalogs are a registration error.\nDefault: []"
    port_groups: Annotated[tuple["PortGroupInput", ...], GraphQLDefault("[]")] = Field(
        validation_alias=AliasChoices("port_groups", "portGroups"),
        serialization_alias="portGroups",
        default=(),
        description="The port groups of the definition. This is used to group ports together in the UI",
    )
    "The port groups of the definition. This is used to group ports together in the UI\nDefault: []"
    args: Annotated[tuple[ArgPortInput, ...], GraphQLDefault("[]")] = Field(
        default=(),
        description="The args of the definition. This is the input ports of the definition",
    )
    "The args of the definition. This is the input ports of the definition\nDefault: []"
    returns: Annotated[tuple["ReturnPortInput", ...], GraphQLDefault("[]")] = Field(
        default=(),
        description="The returns of the definition. This is the output ports of the definition",
    )
    "The returns of the definition. This is the output ports of the definition\nDefault: []"
    kind: ActionKind = Field(
        description="The kind of the definition. This is the type of the definition. Can be either a function or a generator"
    )
    is_test_for: Annotated[tuple["TestTargetInput", ...], GraphQLDefault("[]")] = Field(
        validation_alias=AliasChoices("is_test_for", "isTestFor"),
        serialization_alias="isTestFor",
        default=(),
        description="The actions this definition is a test for, each identified by hash or by (app, key, version).",
    )
    "The actions this definition is a test for, each identified by hash or by (app, key, version).\nDefault: []"
    is_dev: Annotated[bool, GraphQLDefault("False")] = Field(
        validation_alias=AliasChoices("is_dev", "isDev"),
        serialization_alias="isDev",
        default=False,
        description="Whether the definition is a dev definition or not. If the definition is a dev definition, it can be used to create a dev action. If the definition is not a dev definition, it cannot be used to create a dev action",
    )
    "Whether the definition is a dev definition or not. If the definition is a dev definition, it can be used to create a dev action. If the definition is not a dev definition, it cannot be used to create a dev action\nDefault: False"
    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, use_enum_values=True
    )


class DescriptorInput(ActionModel):
    """A single runtime descriptor key/value pair carried by a candidate object."""

    key: str = Field(description="The descriptor key, e.g. 'axes'.")
    value: Any = Field(description="The descriptor value. Any JSON-serializable value.")
    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, use_enum_values=True
    )


class DynamicValueInput(ActionModel):
    """A bound state pointer referencing a variable inside a Blok state instance."""

    literal: str | None = Field(
        default=None,
        description="A static fallback literal value (serialized string or JSON primitive) used when `path` does not resolve.",
    )
    path: str | None = Field(
        default=None,
        description="JSON Pointer to a variable inside the Blok's isolated data model (e.g., '/microscope/exposure').",
    )
    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, use_enum_values=True
    )


class EffectInput(ActionModel):
    """
    An effect is a way to modify a port based on a condition. For example,
    you could have an effect that hides the port if another port meets a condition,
    e.g. when the user selects a certain option in a dropdown, another port is hidden.

    The condition is a pure blok UtilCall (`call`) evaluated client-side against the
    catalog; it must return a boolean deciding whether the effect applies. `dependencies`
    is the authoritative list of other ports the call may reference (plus `value` for the
    port's own value).
    """

    call: "UtilCallInput" = Field(
        description="The pure blok UtilCall, evaluated client-side against the catalog, that decides whether the effect applies. It must return a boolean. Argument value_paths may only reference names listed in `dependencies`, plus `value` for the port's own value."
    )
    dependencies: Annotated[tuple[str, ...], GraphQLDefault("[]")] = Field(
        default=(),
        description="The form-field subscription list of the effect: the keys of the other ports whose values the call may reference. This list is authoritative: a value_path in the call may only reference these names (plus `value` for the port's own value). Use the .. syntax to traverse the tree of ports, e.g. 'foo..bar' for the child 'bar' of port 'foo'.",
    )
    "The form-field subscription list of the effect: the keys of the other ports whose values the call may reference. This list is authoritative: a value_path in the call may only reference these names (plus `value` for the port's own value). Use the .. syntax to traverse the tree of ports, e.g. 'foo..bar' for the child 'bar' of port 'foo'.\nDefault: []"
    message: str | None = Field(
        default=None,
        description="The message to display when the effect is applied (if it is a message effect)",
    )
    kind: EffectKind = Field(
        description="The kind of the effect. Can be either message, hide or custom"
    )
    fade: Annotated[bool, GraphQLDefault("True")] = Field(
        default=True,
        description="Whether to fade out the port when the effect is applied (if it is a hide effect)",
    )
    "Whether to fade out the port when the effect is applied (if it is a hide effect)\nDefault: True"
    source: str | None = Field(
        default=None,
        description="The authoring expression the call was compiled from (informational; never parsed or validated by the server).",
    )
    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, use_enum_values=True
    )


class ImplementAgentInput(ActionModel):
    """Implement an agent with the given implementations, states and locks. This will create the agent if it doesn't exist and update it if it does exist."""

    name: str | None = Field(
        default=None,
        description="The name of the agent. This is used to identify the agent in the system.",
    )
    description: str | None = Field(
        default=None,
        description="What this agent is, in a sentence. Omitting it leaves whatever the agent already has, unlike `name`, which falls back to the client id.",
    )
    states: tuple["StateImplementationInput", ...] | None = Field(
        default=None,
        description="The states of the agent. This is used to specify the initial states of the agent",
    )
    implementations: tuple["ImplementationInput", ...] | None = Field(
        default=None,
        description="The implementations of the agent. This is used to specify the initial implementations of the agent",
    )
    locks: tuple["LockImplementationInput", ...] | None = Field(
        default=None,
        description="The locks of the agent. This is used to specify which resources the agent needs to run",
    )
    bloks: tuple[BlokImplementationInput, ...] | None = Field(
        default=None,
        description="The blocks of the agent. This is used to specify the initial blocks of the agent",
    )
    hash: str | None = Field(
        default=None,
        description="A unique hash of the agent definition. An agent can use this hash to check if its definition has changed and if it needs to update its implementations and states. This is used to optimize the update process by only updating the implementations and states that have changed.",
    )
    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, use_enum_values=True
    )


class ImplementationInput(ActionModel):
    """A implementation is a blueprint for a action. It is composed of a definition, a list of dependencies, and a list of params."""

    definition: DefinitionInput = Field(
        description="The definition of the implementation. This is used to uniquely identify the implementation"
    )
    dependencies: Annotated[tuple[AgentDependencyInput, ...], GraphQLDefault("[]")] = Field(
        default=(), description="The agent dependencies required by this implementation."
    )
    "The agent dependencies required by this implementation.\nDefault: []"
    tracks: tuple["TrackInput", ...] | None = Field(
        default=None,
        description="The tracks of the definition. This is used to track values over time during the runtime of an action. This is the state of a dependency",
    )
    interface: str | None = Field(
        default=None,
        description="The interface of the implementation. This is used to group implementations together in the UI",
    )
    params: Any | None = Field(
        default=None,
        description="The params of the implementation. This is used to pass parameters to the implementation",
    )
    instance_id: str | None = Field(
        validation_alias=AliasChoices("instance_id", "instanceId"),
        serialization_alias="instanceId",
        default=None,
        description="The instance id of the agent this implementation is bound to.",
    )
    locks: tuple[str, ...] | None = Field(
        default=None,
        description="The locks of the implementation. This is used to specify which resources the implementation needs to run",
    )
    optimistics: tuple["OptimisticInput", ...] | None = Field(
        default=None,
        description="The optimistics of the definition. This is used to optimistically set state values when the action is assigned, to provide a better user experience.",
    )
    manipulates: tuple[str, ...] | None = Field(
        default=None,
        description="The states that the implementation manipulates. This is used to identify which states are manipulated by the implementation, and can be use to enhance state safety in the system",
    )
    needs_token: Annotated[bool, GraphQLDefault("True")] = Field(
        validation_alias=AliasChoices("needs_token", "needsToken"),
        serialization_alias="needsToken",
        default=True,
        description="Whether Rekuest should mint a signed provenance token when this implementation is assigned. Default true (provenance-by-default); set false for trivial/internal tasks that never produce external provenance.",
    )
    "Whether Rekuest should mint a signed provenance token when this implementation is assigned. Default true (provenance-by-default); set false for trivial/internal tasks that never produce external provenance.\nDefault: True"
    provenance_audience: tuple[str, ...] | None = Field(
        validation_alias=AliasChoices("provenance_audience", "provenanceAudience"),
        serialization_alias="provenanceAudience",
        default=None,
        description="The downstream service(s) the provenance token should be scoped to (the token's `aud`). If omitted, Rekuest derives the audience from the structures the assignment acts on.",
    )
    effect: Annotated[EffectClass, GraphQLDefault("NONE")] = Field(
        default=EffectClass.NONE,
        description="The effect class of this implementation. NONE work is freely retryable/reclaimable; PHYSICAL work touches the real world and an ambiguous failure is terminal (never retried). Declared by the implementation here — never by the caller.",
    )
    "The effect class of this implementation. NONE work is freely retryable/reclaimable; PHYSICAL work touches the real world and an ambiguous failure is terminal (never retried). Declared by the implementation here — never by the caller.\nDefault: NONE"
    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, use_enum_values=True
    )


class LockDefinitionInput(ActionModel):
    """Which locks does the agent provide in general"""

    key: str = Field(description="The key of the lock. This is used to uniquely identify the lock")
    description: str | None = Field(default=None, description="Describe the lock a bit")
    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, use_enum_values=True
    )


class LockImplementationInput(ActionModel):
    """Which locks does the agent provide in general"""

    key: str = Field(description="The key of the lock implementation.")
    definition: LockDefinitionInput = Field(
        description="The lock definition this implementation fulfills."
    )
    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, use_enum_values=True
    )


class OptimisticInput(ActionModel):
    """An optimistic is used to optimistically set state values when the action is assigned. This is used to provide a better user experience by optimistically setting state values when the action is assigned, instead of waiting for the action to be executed and the state to be updated. This will only ever happen on the frontend."""

    state: str = Field(description="The state to optimistically set when the action is assigned")
    path: str | None = Field(
        default=None,
        description="Static JSON pointer into the state value to set. Mutually exclusive with `path_call`.",
    )
    path_call: "UtilCallInput | None" = Field(
        validation_alias=AliasChoices("path_call", "pathCall"),
        serialization_alias="pathCall",
        default=None,
        description="Pure UtilCall returning the pointer dynamically; may reference `args` (the assignment arguments). Mutually exclusive with `path`.",
    )
    accessor: str | None = Field(
        default=None,
        description="Static JSON pointer into the assignment args for the value to set; omitted: the whole args.",
    )
    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, use_enum_values=True
    )


class PortGroupInput(ActionModel):
    """A Port Group is a group of ports that are related to each other. It is used to group ports together in the UI and provide a better user experience."""

    key: str = Field(
        description="The key of the port group. This is used to uniquely identify the port group"
    )
    title: str | None = Field(
        default=None, description="The title of the port group, displayed in the UI"
    )
    description: str | None = Field(
        default=None, description="The description of the port group, displayed in the UI"
    )
    effects: Annotated[tuple[EffectInput, ...], GraphQLDefault("[]")] = Field(
        default=(), description="The effects applied to the port group as a whole"
    )
    "The effects applied to the port group as a whole\nDefault: []"
    ports: Annotated[tuple[str, ...], GraphQLDefault("[]")] = Field(
        default=(),
        description="The keys of the root arg ports in this group; a port belongs to at most one group",
    )
    "The keys of the root arg ports in this group; a port belongs to at most one group\nDefault: []"
    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, use_enum_values=True
    )


class PortMatchInput(ActionModel):
    """A structural (and optionally object-level) match against a port. Purely
    structural fields target the port shape; the optional ``descriptors`` carry a concrete
    runtime object's key/value pairs, evaluated against the port's compiled requires
    micro-constraint to find actions the object can actually be passed to."""

    at: int | None = Field(default=None, description="The index of the port to match.")
    key: str | None = Field(default=None, description="The key of the port to match.")
    kind: PortKind | None = Field(default=None, description="The kind of the port to match.")
    identifier: str | None = Field(default=None, description="The identifier of the port to match.")
    nullable: bool | None = Field(default=None, description="Whether the port is nullable.")
    dimension: str | None = Field(
        default=None,
        description="The canonical pint dimensionality the port must have (QUANTITY wiring-compatibility key).",
    )
    descriptors: tuple[DescriptorInput, ...] | None = Field(
        default=None,
        description="Runtime descriptors of a candidate object, evaluated against the port's compiled requires micro-constraint. Omit for purely structural matching.",
    )
    children: tuple["PortMatchInput", ...] | None = Field(
        default=None, description="The matches for the children of the port to match."
    )
    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, use_enum_values=True
    )


class ProvidesInput(ActionModel):
    """No documentation"""

    key: str = Field(
        description="The key of the provision: the descriptor name the constraint reads, matched verbatim as one flat key of the candidate object (any non-empty string, e.g. 'axes' or '@mikro/n_space_axes')"
    )
    operator: DescriptorOperator = Field(description="The operator for the provision")
    value: Any | None = Field(
        default=None,
        description="The value of the provision. This can be any JSON serializable value; IN/NOT_IN take a list, LTE/GTE a number, EXISTS none",
    )
    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, use_enum_values=True
    )


class RequiresInput(ActionModel):
    """No documentation"""

    key: str = Field(
        description="The key of the requirement: the descriptor name the constraint reads, matched verbatim as one flat key of the candidate object (any non-empty string, e.g. 'axes' or '@mikro/n_space_axes')"
    )
    operator: DescriptorOperator = Field(description="The operator for the requirement")
    value: Any | None = Field(
        default=None,
        description="The value of the requirement. This can be any JSON serializable value; IN/NOT_IN take a list, LTE/GTE a number, EXISTS none",
    )
    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, use_enum_values=True
    )


class ReturnPortInput(ActionModel):
    """A Port is a single input or output of an action, identified by its `key` and typed by its `kind`.

    STRUCTURE, MEMORY_STRUCTURE and INTERFACE ports carry an `identifier` of the form `@package/key`
    (e.g. `@mikro/image`); ports with the same identifier are compatible. LIST and DICT ports have one
    child (the item type), UNION ports two or more (the variants), MODEL ports one per field. ENUM ports
    declare `choices`. See docs/design/ports.md for the full table.
    """

    key: str = Field(
        description="The key of the port: unique among its siblings, free of '..', not 'value'. LIST/DICT item ports are conventionally keyed '...'."
    )
    label: str | None = Field(
        default=None,
        description="The label of the port. This is the text that is displayed in the UI",
    )
    kind: PortKind = Field(
        description="The kind of the port. This is the type of the port. Can be either int, string, structure, list, bool, dict, float, date, union or model"
    )
    description: str | None = Field(
        default=None,
        description="The description of the port. This is the text that is displayed in the UI when the user hovers over the port",
    )
    identifier: str | None = Field(
        default=None,
        description="The identifier of the port's type, of the form @package/key. Required for STRUCTURE, MEMORY_STRUCTURE and INTERFACE, where it is the only identity a value has; optional for MODEL and ENUM, where it names the class or enum the port was built from so that agents can map a value back to it.",
    )
    nullable: Annotated[bool, GraphQLDefault("False")] = Field(
        default=False,
        description="Whether the port is nullable or not. If the port is nullable, it can be set to null. If the port is not nullable, it cannot be set to null",
    )
    "Whether the port is nullable or not. If the port is nullable, it can be set to null. If the port is not nullable, it cannot be set to null\nDefault: False"
    effects: tuple[EffectInput, ...] | None = Field(
        default=None, description="The effects of the port"
    )
    choices: tuple[ChoiceInput, ...] | None = Field(
        default=None,
        description="The values the port accepts (required for ENUM; optional for INT, FLOAT, STRING). Rendered by CHOICE widgets.",
    )
    reference_unit: str | None = Field(
        validation_alias=AliasChoices("reference_unit", "referenceUnit"),
        serialization_alias="referenceUnit",
        default=None,
        description='For QUANTITY ports: the canonical/reference unit of the physical quantity, e.g. "volt" or "farad". It is the default selection and the key used to resolve the concrete quantity type; other units of the same dimension are still allowed.',
    )
    proposed_units: tuple[str, ...] | None = Field(
        validation_alias=AliasChoices("proposed_units", "proposedUnits"),
        serialization_alias="proposedUnits",
        default=None,
        description='For QUANTITY ports: units offered as a dropdown in the UI, e.g. ["pF", "nF", "uF"]. Proposals only — any unit of the same dimension remains valid input.',
    )
    dimension: str | None = Field(
        default=None,
        description='For QUANTITY ports: the pint dimensionality string, e.g. "[mass] * [length] ** 2 / [time] ** 3 / [current]". This is the wiring-compatibility key between quantity ports.',
    )
    children: tuple["ReturnPortInput", ...] | None = Field(
        default=None, description="The child ports (used for list, dict, union and model ports)."
    )
    widget: "ReturnWidgetInput | None" = Field(
        default=None, description="The return widget to use for this port, discriminated by `kind`."
    )
    provides: tuple[ProvidesInput, ...] | None = Field(
        default=None,
        description="The provisions for the port. Provisions are key-value pairs that can be used to add additional metadata to a port. When using rekuest's action search, you can filter actions based on their port provisions",
    )
    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, use_enum_values=True
    )


ReturnWidgetInput = Annotated[
    ChoiceReturnWidgetInput | CustomReturnWidgetInput, Field(discriminator="kind")
]


class StateAccessorInput(ActionModel):
    """No documentation"""

    option_key: OptionKey = Field(
        validation_alias=AliasChoices("option_key", "optionKey"),
        serialization_alias="optionKey",
        description="The part of the state accessor to use as the value for the assign widget (e.g. the key, the description, the logo, etc.)",
    )
    path: str | None = Field(
        default=None,
        description="Static JSON pointer into the state value ('/x/y'). Omit for the whole value. Mutually exclusive with `call`.",
    )
    call: "UtilCallInput | None" = Field(
        default=None,
        description="Pure UtilCall returning the pointer string dynamically. May reference `state`, `value` and the widget's `dependencies`. Mutually exclusive with `path`.",
    )
    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, use_enum_values=True
    )


class StateDefinitionInput(ActionModel):
    """A state schema is a blueprint for a state. It is composed of a definition, a list of dependencies, and a list of params."""

    ports: tuple[ReturnPortInput, ...] = Field(
        description="The ports of the state schema. This is used to define the structure of the state"
    )
    name: str = Field(
        description="The name of the state schema. This is used to uniquely identify the state schema"
    )
    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, use_enum_values=True
    )


class StateDemandInput(ActionModel):
    """Pure matching criteria for a state definition: hash short-circuits, port
    matches and protocols. Used directly by query filters and, wrapped in a
    StateDependencyInput, by dependency declarations."""

    hash: str | None = Field(
        default=None,
        description="The exact hash of the state definition. When set, matching short-circuits on the hash.",
    )
    key: str | None = Field(
        default=None,
        description="The state's identity key on the agent (defaults to the interface at registration).",
    )
    app: str | None = Field(
        default=None, description="The identifier of the app providing the state."
    )
    matches: tuple[PortMatchInput, ...] | None = Field(
        default=None, description="The matches the state definition's ports must satisfy."
    )
    protocols: tuple[str, ...] | None = Field(
        default=None, description="Protocols (by name) the state must implement."
    )
    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, use_enum_values=True
    )


class StateDependencyInput(ActionModel):
    """A named state requirement of a dependency: a slot key plus the demand the
    agent's state must satisfy, and resolution-lifecycle filters."""

    key: str = Field(
        description="The local slot key of this state requirement — callers reference it when assigning."
    )
    description: str | None = Field(
        default=None,
        description="The description of the dependency, why it is needed and what it is used for.",
    )
    demand: StateDemandInput | None = Field(
        default=None,
        description="The matching criteria the agent's state must satisfy (app/key preferred; matches loosen).",
    )
    optional: Annotated[bool, GraphQLDefault("False")] = Field(
        default=False,
        description="Whether the dependency is optional or not. If the dependency is optional, the agent doesn't have to provide it to be potentially callable",
    )
    "Whether the dependency is optional or not. If the dependency is optional, the agent doesn't have to provide it to be potentially callable\nDefault: False"
    allow_inactive: Annotated[bool, GraphQLDefault("True")] = Field(
        validation_alias=AliasChoices("allow_inactive", "allowInactive"),
        serialization_alias="allowInactive",
        default=True,
        description="Allow inactive nodes, defaults to true",
    )
    "Allow inactive nodes, defaults to true\nDefault: True"
    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, use_enum_values=True
    )


class StateImplementationInput(ActionModel):
    """A state implementation is a blueprint for a state. It is composed of a definition, a list of dependencies, and a list of params."""

    interface: str = Field(
        description="The key of the state implementation. This is used to uniquely identify the state implementation"
    )
    key: str | None = Field(
        default=None,
        description="The stable identity key of the state, matched by state demands. Defaults to the interface when omitted.",
    )
    app: str | None = Field(
        default=None,
        description="The identifier of the app providing this state. Defaults to the registering agent's app identifier when omitted.",
    )
    definition: StateDefinitionInput = Field(
        description="The schema of the state implementation. This is used to define the structure of the state"
    )
    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, use_enum_values=True
    )


class TestTargetInput(ActionModel):
    """A test target: the action a test action tests, identified by exact hash or by an (app, key, version) coordinate. app defaults to the registering agent's app; omitting version matches every version."""

    hash: str | None = Field(default=None, description="The exact hash of the target action.")
    app: str | None = Field(
        default=None,
        description="The app identifier owning the target action. Defaults to the registering agent's app.",
    )
    key: str | None = Field(
        default=None,
        description="The key of the target action. Matches every version unless version is given.",
    )
    version: str | None = Field(
        default=None, description="Restrict a key target to one specific version."
    )
    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, use_enum_values=True
    )


class TrackInput(ActionModel):
    """A value that is being tracked over time during the runtime of an action. This is the state of a dependency"""

    dependency_key: str | None = Field(
        validation_alias=AliasChoices("dependency_key", "dependencyKey"),
        serialization_alias="dependencyKey",
        default=None,
        description="The key of the dependency whose state is being tracked.",
    )
    state_key: str = Field(
        validation_alias=AliasChoices("state_key", "stateKey"),
        serialization_alias="stateKey",
        description="The key of the state to track.",
    )
    value_key: str = Field(
        validation_alias=AliasChoices("value_key", "valueKey"),
        serialization_alias="valueKey",
        description="The key of the value within the state to track.",
    )
    label: str | None = Field(
        default=None, description="An optional human-readable label for the track."
    )
    description: str | None = Field(
        default=None, description="An optional description for the track."
    )
    windows: tuple["WindowInput", ...] | None = Field(
        default=None, description="The windows (aggregations) computed over the tracked value."
    )
    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, use_enum_values=True
    )


class UtilCallInput(ActionModel):
    """Defines a utility call that can be invoked within the system."""

    operation: str = Field(description="The utility function name to invoke.")
    arguments: tuple[ActionArgumentInput, ...] | None = Field(
        default=None, description="Key-value arguments map compiled for the target utility call."
    )
    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, use_enum_values=True
    )


class ValidatorInput(ActionModel):
    """
    A validator for a port. `call` is a pure blok UtilCall evaluated client-side against the
    catalog; it must return a boolean meaning 'valid'. Other ports the call references must be
    listed in `dependencies` (the authoritative subscription list); `value` refers to the port's
    own value. Use the .. syntax when traversing the tree of ports.
    """

    call: UtilCallInput = Field(
        description="The pure blok UtilCall, evaluated client-side against the catalog, that validates the port value. It must return a boolean meaning 'valid'. Argument value_paths may only reference names listed in `dependencies`, plus `value` for the port's own value."
    )
    dependencies: Annotated[tuple[str, ...], GraphQLDefault("[]")] = Field(
        default=(),
        description="The form-field subscription list of the validator: the keys of the other ports whose values the call may reference. This list is authoritative: a value_path in the call may only reference these names (plus `value` for the port's own value). Use the .. syntax to traverse the tree of ports, e.g. 'foo..bar' for the child 'bar' of port 'foo'.",
    )
    "The form-field subscription list of the validator: the keys of the other ports whose values the call may reference. This list is authoritative: a value_path in the call may only reference these names (plus `value` for the port's own value). Use the .. syntax to traverse the tree of ports, e.g. 'foo..bar' for the child 'bar' of port 'foo'.\nDefault: []"
    label: str | None = Field(
        default=None, description="An optional human-readable label for the validator."
    )
    error_message: str | None = Field(
        validation_alias=AliasChoices("error_message", "errorMessage"),
        serialization_alias="errorMessage",
        default=None,
        description="The error message to display when the validation fails",
    )
    source: str | None = Field(
        default=None,
        description="The authoring expression the call was compiled from (informational; never parsed or validated by the server).",
    )
    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, use_enum_values=True
    )


class WindowInput(ActionModel):
    """A window that is calculated"""

    window_function: WindowFunction = Field(
        validation_alias=AliasChoices("window_function", "windowFunction"),
        serialization_alias="windowFunction",
        description="The aggregation to compute over the tracked value within the window.",
    )
    label: str | None = Field(
        default=None, description="An optional human-readable label for the window."
    )
    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, use_enum_values=True
    )


ActionArgumentInput.model_rebuild()
ActionDemandInput.model_rebuild()
AgentDependencyInput.model_rebuild()
ArgPortInput.model_rebuild()
BlokImplementationInput.model_rebuild()
ComponentNodeInput.model_rebuild()
ComponentPropInput.model_rebuild()
CustomAssignWidgetInput.model_rebuild()
CustomReturnWidgetInput.model_rebuild()
DefinitionInput.model_rebuild()
EffectInput.model_rebuild()
ImplementAgentInput.model_rebuild()
ImplementationInput.model_rebuild()
OptimisticInput.model_rebuild()
PortMatchInput.model_rebuild()
ReturnPortInput.model_rebuild()
SearchAssignWidgetInput.model_rebuild()
StateAccessorInput.model_rebuild()
StateChoiceAssignWidgetInput.model_rebuild()
TrackInput.model_rebuild()

#: The fields of a definition that make up its identity, and so its hash.
#: Qualifiers and the like are deliberately absent: changing them must not make an
#: action a different action.
DEFINITION_HASH_KEYS: frozenset[str] = frozenset(
    {
        "name",
        "description",
        "args",
        "returns",
        "stateful",
        "is_test_for",
        "collections",
        "key",
        "version",
        "kind",
        "port_groups",
    }
)


def definition_hash(definition: DefinitionInput) -> str:
    """The stable identity of an action definition (stored by rekuest as ``Action.hash``).

    sha256 over the identity-bearing fields of the definition's plain (snake_case)
    dump, serialised with sorted keys. This is the rekuest server's algorithm; every
    producer and consumer computes it here rather than re-implementing it.
    """
    identity = {
        key: value for key, value in definition.model_dump().items() if key in DEFINITION_HASH_KEYS
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
