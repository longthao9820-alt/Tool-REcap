"""Project layout safety (N4) and architectural dependency boundaries."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from recap_core.domain.errors import UnsafeArtifactPathError
from recap_core.infrastructure.filesystem.project_layout import (
    PROJECT_DIRECTORIES,
    ProjectLayout,
)

CORE_ROOT = Path(__file__).resolve().parents[2] / "recap_core"
FORBIDDEN_IN_DOMAIN = ("fastapi", "sqlite3", "subprocess", "ffmpeg", "requests", "httpx")


def test_create_makes_every_project_directory(tmp_path):
    layout = ProjectLayout(tmp_path / "project").create()
    for relative in PROJECT_DIRECTORIES:
        assert (layout.root / relative).is_dir()


def test_create_is_idempotent(tmp_path):
    ProjectLayout(tmp_path / "project").create()
    ProjectLayout(tmp_path / "project").create()


@pytest.mark.parametrize(
    "relative",
    ["../escape.json", "metadata/../../escape.json", "metadata/../.."],
)
def test_n4_paths_outside_root_are_rejected(tmp_path, relative):
    layout = ProjectLayout(tmp_path / "project").create()
    with pytest.raises(UnsafeArtifactPathError):
        layout.resolve(relative)
    with pytest.raises(UnsafeArtifactPathError):
        layout.write_atomic(relative, b"{}")


def test_n4_absolute_outside_path_is_rejected(tmp_path):
    layout = ProjectLayout(tmp_path / "project").create()
    with pytest.raises(UnsafeArtifactPathError):
        layout.write_atomic(tmp_path / "outside.json", b"{}")
    assert not (tmp_path / "outside.json").exists()


def test_write_atomic_leaves_no_partial_file(tmp_path):
    layout = ProjectLayout(tmp_path / "project").create()
    target = layout.write_atomic("metadata/media.json", b'{"a":1}')
    assert target.read_bytes() == b'{"a":1}'
    assert not list(target.parent.glob("*.partial"))


def test_write_atomic_overwrites_completely(tmp_path):
    layout = ProjectLayout(tmp_path / "project").create()
    layout.write_atomic("metadata/media.json", b"longer-previous-content")
    target = layout.write_atomic("metadata/media.json", b"short")
    assert target.read_bytes() == b"short"


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules


@pytest.mark.parametrize("package", ["domain", "ports"])
def test_domain_and_ports_do_not_depend_on_infrastructure(package):
    checked = 0
    for path in (CORE_ROOT / package).rglob("*.py"):
        checked += 1
        for module in _imported_modules(path):
            root = module.split(".")[0]
            assert root not in FORBIDDEN_IN_DOMAIN, f"{path.name} imports {module}"
            assert "infrastructure" not in module, f"{path.name} imports {module}"
            assert "application" not in module, f"{path.name} imports {module}"
    assert checked > 0
