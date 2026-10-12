"""An app names the files it brings: paths inside its directory, kinds told from the file."""

import hashlib
from pathlib import Path

import pytest

from arkitekt_spec.actions import AssetKind
from arkitekt_spec.declare.app import AppRegistry
from arkitekt_spec.declare.errors import RegistryFrozenError

GLB = b"glTF" + b"\x02\x00\x00\x00" + b"rest"
PNG = b"\x89PNG\r\n\x1a\n" + b"rest"
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 "


@pytest.fixture
def registry(tmp_path: Path) -> AppRegistry:
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "scope.glb").write_bytes(GLB)
    (tmp_path / "logo.png").write_bytes(PNG)
    (tmp_path / "icon.webp").write_bytes(WEBP)
    return AppRegistry(root=tmp_path)


def test_an_asset_is_registered_by_name_and_told_to_the_server_without_its_path(registry: AppRegistry) -> None:
    registry.register_asset("scope", "models/scope.glb")
    registry.register_asset("logo", "logo.png")
    registry.register_asset("icon", "icon.webp")

    declared = registry.to_implement_agent_input(name="app").assets
    assert [(asset.name, asset.kind) for asset in declared] == [("icon", "IMAGE"), ("logo", "IMAGE"), ("scope", "MODEL_3D")]
    scope = declared[2]
    assert scope.ref == "sha256:" + hashlib.sha256(GLB).hexdigest() and scope.size == len(GLB)
    assert set(scope.model_dump()) == {"name", "kind", "ref", "size"}
    assert AssetKind.MODEL_3D.label == "3D model"


def test_an_app_without_assets_declares_none(registry: AppRegistry) -> None:
    assert "assets" not in registry.to_implement_agent_input(name="app").model_dump(exclude_unset=True)


@pytest.mark.parametrize(
    ("name", "path", "kind", "message"),
    [
        ("my-scope", "models/scope.glb", None, "is an identifier"),
        ("a.b", "models/scope.glb", None, "is an identifier"),
        ("scope", "../outside.glb", None, "inside the app's directory"),
        ("scope", "/etc/passwd", None, "inside the app's directory"),
        ("notes", "notes.txt", None, "Cannot tell what kind"),
        ("scope", "logo.png", AssetKind.MODEL_3D, "is not a valid 3D model file"),
        ("logo", "models/scope.glb", AssetKind.IMAGE, "is not a valid image file"),
    ],
)
def test_what_is_not_an_asset_of_the_app_is_refused(registry: AppRegistry, tmp_path: Path, name: str, path: str, kind: AssetKind | None, message: str) -> None:
    (tmp_path / "notes.txt").write_text("no")
    (tmp_path.parent / "outside.glb").write_bytes(GLB)
    with pytest.raises(ValueError, match=message):
        registry.register_asset(name, path, kind)


def test_a_name_is_one_file_and_a_missing_file_is_refused(registry: AppRegistry) -> None:
    registry.register_asset("scope", "models/scope.glb")
    registry.register_asset("scope", "models/scope.glb")
    with pytest.raises(ValueError, match="already registered as scope.glb"):
        registry.register_asset("scope", "logo.png")
    with pytest.raises(FileNotFoundError):
        registry.register_asset("absent", "absent.glb")


def test_assets_travel_into_a_merge_and_a_frozen_registry_takes_no_new_one(registry: AppRegistry) -> None:
    registry.register_asset("scope", "models/scope.glb")
    app = AppRegistry(root=registry.root)

    app.merge(registry)
    assert "scope" in app.registered_assets

    app.freeze()
    app.register_asset("scope", "models/scope.glb")
    with pytest.raises(RegistryFrozenError):
        app.register_asset("logo", "logo.png")


def test_a_blok_names_an_asset_of_its_own_app(registry: AppRegistry) -> None:
    registry.register_asset("scope", "models/scope.glb")
    registry.register_asset("logo", "logo.png")
    registry.register_blok("view", '<Scene><Model src="@self.assets.scope" /></Scene>', context="scene")
    registry.register_blok("panel", '<img src="@self.assets.logo" />')

    bloks = {blok.key: blok for blok in registry.to_implement_agent_input(name="app").bloks}
    (model,) = bloks["view"].components[0].children
    assert model.props[0].dynamic_value.path == "self.assets.scope"
    # The key is one the server maps to this agent, though nothing else of it is used.
    assert [(dependency.key, dependency.state_dependencies) for dependency in bloks["view"].dependencies] == [("self", None)]


@pytest.mark.parametrize(
    ("tree", "message"),
    [
        ('<Scene><Model src="@self.assets.absent" /></Scene>', "which this app has not registered"),
        ('<Scene><Model src="@camera.assets.scope" /></Scene>', "Only an app's own assets"),
        ('<Scene><Model src="@self.assets" /></Scene>', "an asset is named '<dependency>.assets.<name>'"),
        ('<Scene><Model src="@self.assets.scope.glb" /></Scene>', "an asset is named '<dependency>.assets.<name>'"),
        ('<Scene><Model src="sha256:abc" /></Scene>', "is an ASSET prop and must name an asset"),
    ],
)
def test_a_blok_naming_no_asset_of_the_app_is_refused(registry: AppRegistry, tree: str, message: str) -> None:
    registry.register_asset("scope", "models/scope.glb")
    with pytest.raises(ValueError, match=message):
        registry.register_blok("view", tree, context="scene")
        registry.to_implement_agent_input(name="app")


def test_a_changed_file_is_a_changed_declaration(tmp_path: Path) -> None:
    """What the agent hashes to decide whether the server must reconcile it again."""

    def declared(content: bytes) -> dict:
        (tmp_path / "scope.glb").write_bytes(content)
        registry = AppRegistry(root=tmp_path)
        registry.register_asset("scope", "scope.glb")
        return registry.to_implement_agent_input(name="app").model_dump(mode="json", by_alias=True, exclude_none=True)

    assert declared(GLB) == declared(GLB)
    assert declared(GLB) != declared(GLB + b"re-exported")


def test_no_state_can_be_called_what_names_the_assets(registry: AppRegistry) -> None:
    with pytest.raises(ValueError, match="A state cannot be called 'assets'"):

        @registry.state(name="assets")
        class Files:
            count: int


def test_an_app_dump_lists_its_assets_with_their_paths_and_hashes(registry: AppRegistry) -> None:
    """What a built image records: where each file is in the app, and what its bytes are."""
    from arkitekt_spec.declaration import AppDeclaration
    from arkitekt_spec.manifest import AppManifest

    registry.register_asset("scope", "models/scope.glb")
    registry.register_asset("logo", "logo.png")
    declaration = AppDeclaration(manifest=AppManifest(identifier="app", version="1.0.0", scopes=[]), assets=registry.snapshot().get_inspected_assets())

    dumped = declaration.to_inspection().model_dump(mode="json")["assets"]
    assert dumped == [
        {"name": "logo", "kind": "IMAGE", "ref": "sha256:" + hashlib.sha256(PNG).hexdigest(), "size": len(PNG), "path": "logo.png"},
        {"name": "scope", "kind": "MODEL_3D", "ref": "sha256:" + hashlib.sha256(GLB).hexdigest(), "size": len(GLB), "path": "models/scope.glb"},
    ]
    # The server is told everything but where the files are on this machine.
    assert [set(asset.model_dump()) for asset in declaration.to_agent_input().assets] == [{"name", "kind", "ref", "size"}] * 2
