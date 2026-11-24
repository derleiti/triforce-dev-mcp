#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from urllib.parse import urlparse

DEB_RE = re.compile(
    r"^(?P<type>deb(?:-(?P<type_arch>[A-Za-z0-9_-]+))?)\s+"
    r"(?:\[(?P<options>[^\]]+)\]\s+)?"
    r"(?P<url>\S+)\s+(?P<suite>\S+)(?:\s+(?P<components>.*))?$"
)

LABELS = [
    ("archive.ubuntu.com/ubuntu", "Ubuntu Archive", "ubuntu", "ubuntu"),
    ("security.ubuntu.com/ubuntu", "Ubuntu Security", "ubuntu", "ubuntu-security"),
    ("deb.nodesource.com/node_22.x", "NodeSource 22.x", "dev", "nodesource-22"),
    ("deb.nodesource.com/node_20.x", "NodeSource 20.x", "dev", "nodesource-20"),
    ("dl.google.com/linux/chrome/deb", "Google Chrome", "desktop", "chrome"),
    ("downloads.claude.ai/claude-desktop/apt/stable", "Claude Desktop", "ai", "claude-desktop"),
    ("us-central1-apt.pkg.dev/projects/antigravity-auto-updater-dev", "Google Antigravity", "ai", "antigravity"),
    ("packages.mozilla.org/apt", "Mozilla", "desktop", "mozilla"),
    ("repo.radeon.com/rocm/apt/7.2", "AMD ROCm 7.2", "drivers", "rocm-7-2"),
    ("repo.radeon.com/graphics/7.2/ubuntu", "AMD Graphics 7.2", "drivers", "amd-graphics-7-2"),
    ("linux.teamviewer.com/deb", "TeamViewer", "desktop", "teamviewer"),
    ("downloads.cursor.com/aptrepo", "Cursor", "dev", "cursor"),
    ("windsurf-stable.codeiumdata.com/wVxQEIWkwPUEAGf3/apt", "Windsurf", "dev", "windsurf"),
    ("dl.winehq.org/wine-builds/ubuntu", "WineHQ", "gaming", "winehq"),
    ("download.docker.com/linux/ubuntu", "Docker CE", "dev", "docker"),
    ("ppa.launchpadcontent.net/cappelikan/ppa/ubuntu", "Cappelikan PPA", "dev", "cappelikan"),
    ("ppa.launchpadcontent.net/kisak/kisak-mesa/ubuntu", "Kisak Mesa", "drivers", "kisak-mesa"),
    ("ppa.launchpadcontent.net/graphics-drivers/ppa/ubuntu", "Graphics Drivers PPA", "drivers", "graphics-drivers"),
    ("ppa.launchpadcontent.net/libreoffice/ppa/ubuntu", "LibreOffice PPA", "desktop", "libreoffice"),
    ("repo.steampowered.com/steam", "Steam", "gaming", "steam"),
    ("developer.download.nvidia.com/compute/cuda/repos/ubuntu2604/x86_64", "NVIDIA CUDA 26.04", "ai", "cuda"),
    ("archive.neon.kde.org/stable", "KDE neon Stable", "desktop", "kde-neon"),
    ("hub.unity3d.com/linux/repos/deb", "Unity Hub", "dev", "unity"),
    ("packages.microsoft.com/repos/code", "Visual Studio Code", "dev", "vscode"),
]

DISTRO_SPECIFIC_PREFIXES = (
    "archive.ubuntu.com/ubuntu",
    "security.ubuntu.com/ubuntu",
    "dl.winehq.org/wine-builds/ubuntu",
    "download.docker.com/linux/ubuntu",
    "ppa.launchpadcontent.net/",
    "archive.neon.kde.org/",
)


def slug(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return value or "repo"


def repo_path(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.netloc}{parsed.path}".strip("/")


def parse_default_arch(lines: list[str]) -> str:
    for raw in lines:
        m = re.match(r"^\s*set\s+defaultarch\s+(\S+)", raw)
        if m:
            return m.group(1).strip("\"'")
    return "amd64"


def configured_arches(match: re.Match[str], default_arch: str) -> list[str]:
    type_arch = match.group("type_arch")
    if type_arch:
        return [type_arch]
    options = match.group("options") or ""
    arch_match = re.search(r"(?:^|\s)arch=([^\s]+)", options)
    if arch_match:
        return [a for a in arch_match.group(1).split(",") if a]
    return [default_arch]


def friendly(path: str) -> tuple[str, str, str]:
    for prefix, label, category, base_id in LABELS:
        if path == prefix or path.startswith(prefix + "/"):
            return label, category, base_id
    host = path.split("/", 1)[0]
    return host, "other", slug(path)


def suite_target_codename(path: str, suite: str) -> str:
    if "ubuntu2604" in path:
        return "resolute"
    if suite == "/":
        return ""
    if path.startswith(DISTRO_SPECIFIC_PREFIXES):
        for suffix in ("-updates", "-security", "-backports", "-proposed"):
            if suite.endswith(suffix):
                return suite[: -len(suffix)]
        return suite
    return ""


def available_arches(repo_root: Path, suite: str, configured: list[str],
                     components: list[str] | None = None) -> list[str]:
    if suite == "/":
        if not any((repo_root / name).is_file() for name in ("Release", "InRelease")):
            return []
        if not any((repo_root / name).is_file() for name in ("Packages", "Packages.gz", "Packages.xz")):
            return []
        if "x86_64" in repo_root.as_posix() and "amd64" in configured:
            return ["amd64"]
        return configured[:1]

    suite_root = repo_root / "dists" / suite
    if not ((suite_root / "Release").is_file() or (suite_root / "InRelease").is_file()):
        return []

    selected = components if components is not None else sorted(
        p.name for p in suite_root.iterdir() if p.is_dir()
    )
    if not selected:
        return []
    # Each advertised arch must have an index in every advertised component.
    # A union would publish a Cartesian product of nonexistent indexes.
    per_component = []
    for component in selected:
        per_component.append({
            p.name.removeprefix("binary-")
            for p in (suite_root / component).glob("binary-*")
            if any((p / name).is_file() for name in ("Packages", "Packages.gz", "Packages.xz"))
        })
    found = set.intersection(*per_component)
    if configured:
        return [arch for arch in configured if arch in found]
    return sorted(found)


def make_id(base_id: str, suite: str) -> str:
    if suite == "/":
        return base_id
    return f"{base_id}-{slug(suite)}"


def parse_mirror_list(mirror_list: Path, mirror_root: Path) -> list[dict[str, str]]:
    lines = mirror_list.read_text(encoding="utf-8").splitlines()
    default_arch = parse_default_arch(lines)
    merged: dict[tuple[str, str, str], dict[str, object]] = {}

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or not line.startswith("deb"):
            continue
        match = DEB_RE.match(line)
        if not match:
            continue
        url = match.group("url").rstrip("/")
        suite = match.group("suite")
        if suite == "./":
            suite = "/"
        components = " ".join((match.group("components") or "").split())
        path = repo_path(url)
        key = (path, suite, components)
        entry = merged.setdefault(
            key,
            {
                "path": path,
                "suite": suite,
                "components": components,
                "source_url": url,
                "configured_arches": [],
            },
        )
        for arch in configured_arches(match, default_arch):
            if arch not in entry["configured_arches"]:
                entry["configured_arches"].append(arch)

    rows: list[dict[str, str]] = []
    for entry in merged.values():
        path = str(entry["path"])
        suite = str(entry["suite"])
        repo_root = mirror_root / path
        arches = available_arches(repo_root, suite, list(entry["configured_arches"]),
                                  str(entry["components"]).split())
        if not arches:
            continue
        label, category, base_id = friendly(path)
        rows.append(
            {
                "id": make_id(base_id, suite),
                "label": label,
                "category": category,
                "path": path,
                "suite": suite,
                "components": str(entry["components"]) or "-",
                "architectures": ",".join(arches),
                "source_url": str(entry["source_url"]),
                "target_codename": suite_target_codename(path, suite) or "*",
            }
        )
    return rows


def add_local_repo(rows: list[dict[str, str]], mirror_root: Path) -> None:
    local_root = mirror_root / "repo.ailinux.me"
    dists = local_root / "dists"
    if not dists.is_dir():
        return
    for suite_root in sorted(p for p in dists.iterdir() if p.is_dir()):
        suite = suite_root.name
        components = sorted(
            p.name for p in suite_root.iterdir()
            if p.is_dir() and any(p.glob("binary-*"))
        )
        arches = available_arches(local_root, suite, [], components)
        if not arches:
            continue
        rows.append(
            {
                "id": f"ailinux-{slug(suite)}",
                "label": "AILinux Local",
                "category": "ailinux",
                "path": "repo.ailinux.me",
                "suite": suite,
                "components": " ".join(components) or "main",
                "architectures": ",".join(sorted(arches)),
                "source_url": "local://repo.ailinux.me",
                "target_codename": suite,
            }
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate AILinux mirror repository manifest")
    parser.add_argument("--mirror-list", required=True, type=Path)
    parser.add_argument("--mirror-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()

    rows = parse_mirror_list(args.mirror_list, args.mirror_root)
    add_local_repo(rows, args.mirror_root)
    rows.sort(key=lambda row: (row["category"], row["label"], row["suite"], row["id"]))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "id",
        "label",
        "category",
        "path",
        "suite",
        "components",
        "architectures",
        "source_url",
        "target_codename",
    ]
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    if args.json_output:
        args.json_output.write_text(json.dumps({"version": 1, "repos": rows}, indent=2) + "\n", encoding="utf-8")

    print(f"[mirror-manifest] {len(rows)} repository entries -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
