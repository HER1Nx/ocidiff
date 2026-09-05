import io
import json
import tarfile
from dataclasses import dataclass

import httpx

REGISTRY = "https://registry-1.docker.io"
AUTH = "https://auth.docker.io/token"
TIMEOUT = 60
ACCEPT = ",".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
])
DPKG_STATUS_PATH = "var/lib/dpkg/status"


class ImgdiffError(Exception):
    ""


def get_token(repo: str) -> str:
    r = httpx.get(
        AUTH,
        params={"service": "registry.docker.io", "scope": f"repository:{repo}:pull"},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.json()["token"]


def get_manifest(repo: str, reference: str, token: str) -> dict:
    r = httpx.get(
        f"{REGISTRY}/v2/{repo}/manifests/{reference}",
        headers={"Authorization": f"Bearer {token}", "Accept": ACCEPT},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


def get_blob(repo: str, digest: str, token: str) -> bytes:
    r = httpx.get(
        f"{REGISTRY}/v2/{repo}/blobs/{digest}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=TIMEOUT,
        follow_redirects=True,
    )
    r.raise_for_status()
    return r.content


def pick_amd64(index: dict) -> str:
    for entry in index.get("manifests", []):
        platform = entry.get("platform", {})
        if platform.get("architecture") == "amd64" and platform.get("os") == "linux":
            return entry["digest"]
    raise ImgdiffError("no linux/amd64 variant for this image")


@dataclass
class Image:
    repo: str
    tag: str
    manifest: dict
    config: dict
    token: str

    @property
    def name(self) -> str:
        return f"{self.repo}:{self.tag}"


def parse_reference(text: str) -> tuple[str, str]:
    """'nginx' -> ('library/nginx', 'latest'). An unqualified name gets 'library/'."""
    repo, _, tag = text.partition(":")
    if "/" not in repo:
        repo = f"library/{repo}"
    return repo, tag or "latest"


def fetch_image(text: str) -> Image:
    repo, tag = parse_reference(text)
    try:
        token = get_token(repo)
        manifest = get_manifest(repo, tag, token)
        if "manifests" in manifest:
            manifest = get_manifest(repo, pick_amd64(manifest), token)
        config = json.loads(get_blob(repo, manifest["config"]["digest"], token))
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise ImgdiffError(f"image not found: {repo}:{tag}") from e
        raise ImgdiffError(f"Docker Hub refused the request: {e.response.status_code}") from e
    return Image(repo, tag, manifest, config, token)


def size_mb(img: Image) -> float:
    return sum(layer["size"] for layer in img.manifest["layers"]) / 1024 / 1024


def layer_count(img: Image) -> int:
    return len(img.manifest["layers"])


def read_env(img: Image) -> dict[str, str]:
    lines = img.config.get("config", {}).get("Env", [])
    return dict(line.split("=", 1) for line in lines if "=" in line)


def find_file(img: Image, path: str) -> str | None:
    """Search the layers top down, so the most recent copy wins."""
    for layer in reversed(img.manifest["layers"]):
        raw = get_blob(img.repo, layer["digest"], img.token)
        try:
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r|gz") as tar:
                for member in tar:
                    if member.name.lstrip("./") == path:
                        return tar.extractfile(member).read().decode("utf-8", "replace")
        except tarfile.TarError:
            continue
    return None


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


def compare(img_a: Image, img_b: Image) -> dict:
    mb_a, mb_b = size_mb(img_a), size_mb(img_b)
    return {
        "a": img_a.name,
        "b": img_b.name,
        "size_mb": {"a": mb_a, "b": mb_b, "delta": mb_b - mb_a},
        "layers": {"a": layer_count(img_a), "b": layer_count(img_b)},
        "env": diff_dicts(read_env(img_a), read_env(img_b)),
    }
