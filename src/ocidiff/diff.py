"""Comparing two images. Only read_packages reaches the network, and it is the slow part."""

from dataclasses import dataclass

from ocidiff.registry import DPKG_STATUS_PATH, Image, find_file


def size_mib(img: Image) -> float:
    return sum(layer["size"] for layer in img.manifest["layers"]) / 1024 / 1024


def layer_count(img: Image) -> int:
    return len(img.manifest["layers"])


def read_env(img: Image) -> dict[str, str]:
    lines = img.config.get("config", {}).get("Env", [])
    return dict(line.split("=", 1) for line in lines if "=" in line)


def parse_dpkg_status(text: str) -> dict[str, str]:
    packages, name = {}, None
    for line in text.splitlines():
        if line.startswith("Package: "):
            name = line.removeprefix("Package: ").strip()
        elif name and line.startswith("Version: "):
            packages[name] = line.removeprefix("Version: ").strip()
            name = None
    return packages


def read_packages(img: Image) -> dict[str, str] | None:
    """{package: version}, or None when the image is not Debian based."""
    status = find_file(img, DPKG_STATUS_PATH)
    return None if status is None else parse_dpkg_status(status)


@dataclass
class Change:
    kind: str
    name: str
    before: str | None
    after: str | None


def diff_dicts(a: dict, b: dict) -> list[Change]:
    changes = []
    for key in sorted(a.keys() | b.keys()):
        before, after = a.get(key), b.get(key)
        if before == after:
            continue
        kind = "-" if after is None else "+" if before is None else "~"
        changes.append(Change(kind, key, before, after))
    return changes


@dataclass
class Report:
    a: str
    b: str
    size_a: float
    size_b: float
    layers_a: int
    layers_b: int
    env: list[Change]
    packages: list[Change] | None = None
    packages_note: str = ""


def compare(img_a: Image, img_b: Image) -> Report:
    return Report(
        a=img_a.name,
        b=img_b.name,
        size_a=size_mib(img_a),
        size_b=size_mib(img_b),
        layers_a=layer_count(img_a),
        layers_b=layer_count(img_b),
        env=diff_dicts(read_env(img_a), read_env(img_b)),
    )


def compare_packages(img_a: Image, img_b: Image) -> list[Change] | None:
    """None when either image is not Debian based. Downloads layers, so it is the slow half."""
    pkgs_a = read_packages(img_a)
    pkgs_b = read_packages(img_b)
    if pkgs_a is None or pkgs_b is None:
        return None
    return diff_dicts(pkgs_a, pkgs_b)
