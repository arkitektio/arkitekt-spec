"""The selector vocabulary: the hardware and capability requirements of a flavour.

Semantics:

- ``required: true`` is a hard constraint: a deployer MUST NOT place the flavour on a
  backend that fails it (Kubernetes' ``requiredDuringSchedulingIgnoredDuringExecution``).
- ``required: false`` marks a preference, scored by ``weight``. Deployers SHOULD prefer
  the candidate with the highest sum of satisfied preferred weights.
- ``label`` selectors match a backend resource's ``qualifiers`` (the nodeSelector
  analog). A ``null`` value means "the key exists".
- Selectors are only stored and served; evaluating them is the deployer's job.

A service dependency is NEVER a selector: the services an app needs (mikro, rekuest,
...) are :class:`~arkitekt_spec.manifest.Requirement`\\ s. Selectors only constrain
*hardware placement*.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class BaseSelector(BaseModel):
    """The fields every selector kind shares: the hard/soft split.

    Unlike the envelope models, selectors REFUSE unknown keys. They are written by
    hand into a flavour's ``config.yaml``, where a typo must not silently widen a
    placement constraint, and a discriminated union relies on it to turn a field
    that contradicts ``kind`` into an error naming both.
    """

    required: bool = Field(
        default=True,
        description="If true, a backend failing this selector must not run the flavour "
        "(hard constraint). If false, the selector is a preference scored by `weight`.",
    )
    weight: int = Field(
        default=1,
        description="Scoring weight of a preferred (non-required) selector; deployers "
        "prefer candidates with the highest sum of satisfied weights.",
    )

    model_config = ConfigDict(extra="forbid", validate_by_name=True, validate_by_alias=True)


class CpuSelector(BaseSelector):
    """CPU requirements of the flavour."""

    kind: Literal["cpu"] = "cpu"
    min_count: int | None = Field(
        default=None, alias="minCount", description="The minimum number of CPU cores required."
    )
    frequency: float | None = Field(
        default=None, description="The minimum CPU frequency required, in MHz."
    )
    arch: str | None = Field(
        default=None,
        description="The CPU architecture the image is built for (docker platform / "
        "kubernetes.io/arch values, e.g. 'amd64', 'arm64').",
    )


class RamSelector(BaseSelector):
    """System-memory requirements of the flavour."""

    kind: Literal["ram"] = "ram"
    min: int | None = Field(
        default=None, description="The minimum amount of system memory required, in MB."
    )


class CudaSelector(BaseSelector):
    """Requires a CUDA-capable (NVIDIA) GPU."""

    kind: Literal["cuda"] = "cuda"
    compute_capability: str | None = Field(
        default=None,
        alias="computeCapability",
        description="The minimum CUDA compute capability required (e.g. '8.6') — NVIDIA's "
        "standard placement key.",
    )
    cuda_version: str | None = Field(
        default=None,
        alias="cudaVersion",
        description="The minimum CUDA (driver/runtime) version required.",
    )
    memory: int | None = Field(
        default=None, description="The minimum GPU memory (VRAM) required, in MB."
    )
    count: int | None = Field(
        default=None,
        description="The number of GPUs required (a Docker device-reservation count; "
        "unset lets the deployer decide).",
    )
    cuda_cores: int | None = Field(
        default=None,
        alias="cudaCores",
        description="Deprecated: the minimum number of CUDA cores. Prefer "
        "computeCapability and memory.",
    )


class RocmSelector(BaseSelector):
    """Requires a ROCm-capable (AMD) GPU."""

    kind: Literal["rocm"] = "rocm"
    api_version: str | None = Field(
        default=None, alias="apiVersion", description="The minimum ROCm API version required."
    )
    api_thing: str | None = Field(
        default=None, alias="apiThing", description="An additional ROCm capability qualifier."
    )


class OneApiSelector(BaseSelector):
    """Requires a oneAPI-capable (Intel) accelerator."""

    kind: Literal["oneapi"] = "oneapi"
    oneapi_version: str | None = Field(
        default=None, alias="oneapiVersion", description="The minimum oneAPI version required."
    )


class LabelSelector(BaseSelector):
    """Requires the backend resource to carry a qualifier: the nodeSelector analog."""

    kind: Literal["label"] = "label"
    key: str = Field(description="The qualifier key the backend resource must carry.")
    value: str | None = Field(
        default=None,
        description="The value the qualifier must have; null means the key merely has to exist.",
    )


Selector = Annotated[
    CpuSelector | RamSelector | CudaSelector | RocmSelector | OneApiSelector | LabelSelector,
    Field(discriminator="kind"),
]
"""Any selector, dispatched on ``kind``."""

#: Every selector class, keyed by its ``kind``.
SELECTOR_KINDS: dict[str, type[BaseSelector]] = {
    "cpu": CpuSelector,
    "ram": RamSelector,
    "cuda": CudaSelector,
    "rocm": RocmSelector,
    "oneapi": OneApiSelector,
    "label": LabelSelector,
}
