"""Who an app is: its identity, and the services it needs."""

from pydantic import Field

from arkitekt_spec.base import WireModel

#: The author recorded for an app that names none.
UNKNOWN_AUTHOR = "unknown"

#: The target (``module[:attr]``) an image runs when it names none.
DEFAULT_ENTRYPOINT = "app"


class Requirement(WireModel):
    """A service instance the app needs, filled in by the platform at login.

    A requirement is never a hardware constraint: those are selectors.
    """

    key: str = Field(description="The key the app looks the service up by.")
    service: str = Field(
        description="The service type that fills the key, in reverse-domain form "
        "(e.g. 'live.arkitekt.mikro')."
    )
    optional: bool = Field(
        default=False,
        description="Whether the app still works when no instance of the service is available.",
    )
    description: str | None = Field(
        default=None, description="Why the app needs the service, shown to the user."
    )


class AppManifest(WireModel):
    """The identity of one released version of an app."""

    identifier: str = Field(description="The globally unique identifier of the app.")
    version: str = Field(description="The version of this release.")
    author: str = Field(default=UNKNOWN_AUTHOR, description="Who publishes the app.")
    description: str | None = Field(
        default=None, description="What the app is, in a sentence or two."
    )
    logo: str | None = Field(default=None, description="A URL to the app's logo.")
    scopes: list[str] = Field(
        default_factory=list, description="The scopes the app requests from the user."
    )
    entrypoint: str = Field(
        default=DEFAULT_ENTRYPOINT,
        description="The target (`module[:attr]`) the image runs to find the app.",
    )

    def to_console_string(self) -> str:
        """A one-line rendering for CLIs and logs."""
        return f"📦 {self.identifier} ({self.version}) by {self.author}"
