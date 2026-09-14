from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "docker" / "repository" / "generate-mirror-manifest.py"


def load_generator():
    spec = importlib.util.spec_from_file_location("mirror_manifest", GENERATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_manifest_requires_real_dists_metadata(tmp_path: Path):
    mod = load_generator()
    mirror = tmp_path / "mirror"
    root = mirror / "example.invalid" / "repo"
    (root / "dists" / "resolute" / "main" / "binary-amd64").mkdir(parents=True)
    mirror_list = tmp_path / "mirror.list"
    mirror_list.write_text("set defaultarch amd64\ndeb [arch=amd64] https://example.invalid/repo resolute main\n")
    assert mod.parse_mirror_list(mirror_list, mirror) == []
    (root / "dists" / "resolute" / "InRelease").write_text("signed")
    (root / "dists" / "resolute" / "main" / "binary-amd64" / "Packages.xz").write_bytes(b"x")
    rows = mod.parse_mirror_list(mirror_list, mirror)
    assert len(rows) == 1
    assert rows[0]["suite"] == "resolute"
    assert rows[0]["architectures"] == "amd64"


def test_manifest_supports_flat_repository_only_with_release_and_packages(tmp_path: Path):
    mod = load_generator()
    mirror = tmp_path / "mirror"
    root = mirror / "flat.example" / "repo"
    root.mkdir(parents=True)
    mirror_list = tmp_path / "mirror.list"
    mirror_list.write_text("set defaultarch amd64\ndeb [arch=amd64] https://flat.example/repo /\n")
    assert mod.parse_mirror_list(mirror_list, mirror) == []
    (root / "Release").write_text("Origin: Flat\n")
    assert mod.parse_mirror_list(mirror_list, mirror) == []
    (root / "Packages.gz").write_bytes(b"x")
    rows = mod.parse_mirror_list(mirror_list, mirror)
    assert len(rows) == 1
    assert rows[0]["suite"] == "/"
    assert rows[0]["components"] == "-"


def test_resolute_target_matching_and_cuda_mapping():
    mod = load_generator()
    assert mod.suite_target_codename("download.docker.com/linux/ubuntu", "resolute") == "resolute"
    assert mod.suite_target_codename("archive.neon.kde.org/stable", "resolute") == "resolute"
    assert mod.suite_target_codename("developer.download.nvidia.com/compute/cuda/repos/ubuntu2604/x86_64", "/") == "resolute"


def test_generated_ids_are_unique_for_current_mirror():
    mod = load_generator()
    rows = mod.parse_mirror_list(
        ROOT / "docker" / "repository" / "mirror.list",
        ROOT / "docker" / "repository" / "repo" / "mirror",
    )
    mod.add_local_repo(rows, ROOT / "docker" / "repository" / "repo" / "mirror")
    ids = [row["id"] for row in rows]
    assert len(ids) == len(set(ids))
    for row in rows:
        assert row["path"]
        assert row["suite"]
        assert row["architectures"]
        assert row["source_url"]
        assert row["target_codename"]


def test_legacy_repo_installers_are_thin_wrappers_to_canonical_client():
    canonical = ROOT / "docker" / "repository" / "add-ailinux-repo.sh"
    assert canonical.is_file()
    for wrapper in (
        ROOT / "scripts" / "docker" / "repository" / "add-ailinux-repo.sh",
        ROOT / "scripts" / "docker" / "repository" / "client" / "add-ailinux-repo.sh",
    ):
        text = wrapper.read_text()
        assert "docker/repository/add-ailinux-repo.sh" in text
        assert "exec \"$CANONICAL\" \"$@\"" in text
        assert len(text.splitlines()) < 20


def test_canonical_client_exposes_manifest_modes_and_signed_by():
    text = (ROOT / "docker" / "repository" / "add-ailinux-repo.sh").read_text()
    for flag in ("--list-repos", "--dry-run", "--installed-only", "--select"):
        assert flag in text
    assert "signed-by=${KEYRING_PATH}" in text
    assert 'if [[ "$suite" == "/" ]]' in text
    assert "repo_matches_os" in text
