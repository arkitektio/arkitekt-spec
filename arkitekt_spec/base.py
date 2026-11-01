"""The model base every envelope type shares."""

from importlib.metadata import PackageNotFoundError, version

from pydantic import BaseModel, ConfigDict


def current_spec_version() -> str:
    """The arkitekt-spec writing a file now, which is what a reader picks its parser by."""
    try:
        return version("arkitekt-spec")
    except PackageNotFoundError:
        # A source checkout on the path. A released package always has its metadata.
        return "0+local"


class WireModel(BaseModel):
    """A model that is read across versions.

    Every envelope model is written by one version of the spec and may be read by
    another: an app image's inspection is produced inside the container (whatever
    arkitekt it installed) and read by the host CLI, and ``deployments.yaml`` is
    written by the CLI and read by a kabinet server that upgrades on its own
    schedule. So an unknown key is IGNORED, not refused: adding a field is a minor
    change an older reader survives. Every file names the spec that wrote it in
    ``spec_version``, and so does each inspection: a reader picks its parser by that.

    Refusing unknown keys here is exactly what broke builds when rekuest started
    emitting an agent ``description`` that kabinet's ``InspectionInput`` did not know.

    Field names are snake_case in Python; where the on-disk format has always been
    camelCase, the field carries an alias, and both spellings are accepted on read.
    A plain ``model_dump()`` stays snake_case (consumers store it as JSON); files are
    written with ``by_alias=True`` -- see :func:`arkitekt_spec.dump_deployments`.
    """

    model_config = ConfigDict(
        extra="ignore",
        validate_by_name=True,
        validate_by_alias=True,
    )
