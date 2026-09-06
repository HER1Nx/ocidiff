import io
import tarfile
from types import SimpleNamespace

import httpx
import pytest

from ocidiff import OcidiffError, registry

DPKG_PATH = "var/lib/dpkg/status"


def response(payload=None, content=b"", status=200) -> SimpleNamespace:
    """Enough of an httpx.Response for the three functions that touch one."""

    def raise_for_status():
        if status >= 400:
            request = httpx.Request("GET", "https://registry.test")
            raise httpx.HTTPStatusError(
                "boom", request=request, response=httpx.Response(status, request=request)
            )

    return SimpleNamespace(json=lambda: payload, content=content, raise_for_status=raise_for_status)


def tar_gz(name: str, body: str) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = body.encode()
        info = tarfile.TarInfo(name)
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def image(layers: list[dict]) -> registry.Image:
    return registry.Image(
        repo="library/x", tag="1", manifest={"layers": layers}, config={}, token=""
    )


def test_parse_reference_qualifies_an_official_name():
    assert registry.parse_reference("nginx") == ("library/nginx", "latest")


def test_parse_reference_keeps_an_explicit_tag():
    assert registry.parse_reference("nginx:1.27.0") == ("library/nginx", "1.27.0")


def test_parse_reference_leaves_a_namespaced_repo_alone():
    assert registry.parse_reference("bitnami/nginx:latest") == ("bitnami/nginx", "latest")


def test_parse_reference_defaults_a_namespaced_repo_to_latest():
    assert registry.parse_reference("bitnami/nginx") == ("bitnami/nginx", "latest")


def test_pick_amd64_finds_the_linux_entry_wherever_it_sits():
    index = {
        "manifests": [
            {"platform": {"architecture": "arm64", "os": "linux"}, "digest": "sha256:arm"},
            {"platform": {"architecture": "amd64", "os": "windows"}, "digest": "sha256:win"},
            {"platform": {"architecture": "amd64", "os": "linux"}, "digest": "sha256:amd"},
            {"platform": {"architecture": "unknown", "os": "unknown"}, "digest": "sha256:att"},
        ]
    }
    assert registry.pick_amd64(index) == "sha256:amd"


def test_pick_amd64_raises_when_the_image_has_no_amd64_build():
    index = {"manifests": [{"platform": {"architecture": "arm64", "os": "linux"}, "digest": "x"}]}
    with pytest.raises(OcidiffError, match="no linux/amd64"):
        registry.pick_amd64(index)


def test_fetch_image_follows_the_manifest_list_to_the_amd64_entry(monkeypatch):
    index = {
        "manifests": [
            {"platform": {"architecture": "arm64", "os": "linux"}, "digest": "sha256:arm"},
            {"platform": {"architecture": "amd64", "os": "linux"}, "digest": "sha256:amd"},
        ]
    }
    manifest = {"config": {"digest": "sha256:cfg"}, "layers": [{"size": 1, "digest": "sha256:l"}]}
    asked = []

    def fake_get(url, **kwargs):
        asked.append(url)
        if url == registry.AUTH:
            return response({"token": "t"})
        if url.endswith("/manifests/1.27.0"):
            return response(index)
        if url.endswith("/manifests/sha256:amd"):
            return response(manifest)
        if url.endswith("/blobs/sha256:cfg"):
            return response(content=b'{"config": {"Env": ["A=1"]}}')
        raise AssertionError(f"unexpected request: {url}")

    monkeypatch.setattr(registry.httpx, "get", fake_get)
    img = registry.fetch_image("nginx:1.27.0")

    assert img.manifest == manifest
    assert img.name == "library/nginx:1.27.0"
    assert any(u.endswith("/manifests/sha256:amd") for u in asked)
    assert not any(u.endswith("/manifests/sha256:arm") for u in asked)


def test_fetch_image_turns_a_404_into_a_readable_error(monkeypatch):
    def fake_get(url, **kwargs):
        return response({"token": "t"}) if url == registry.AUTH else response(status=404)

    monkeypatch.setattr(registry.httpx, "get", fake_get)
    with pytest.raises(OcidiffError, match="image not found: library/nginx:nope"):
        registry.fetch_image("nginx:nope")


def test_fetch_image_reports_any_other_refusal_with_its_status(monkeypatch):
    def fake_get(url, **kwargs):
        return response({"token": "t"}) if url == registry.AUTH else response(status=429)

    monkeypatch.setattr(registry.httpx, "get", fake_get)
    with pytest.raises(OcidiffError, match="refused the request: 429"):
        registry.fetch_image("nginx:latest")


def test_find_file_returns_the_copy_from_the_newest_layer(monkeypatch):
    blobs = {
        "sha256:old": tar_gz(f"./{DPKG_PATH}", "Package: nginx\nVersion: 1.0\n"),
        "sha256:new": tar_gz(f"./{DPKG_PATH}", "Package: nginx\nVersion: 2.0\n"),
    }
    monkeypatch.setattr(registry, "get_blob", lambda repo, digest, token: blobs[digest])
    img = image([{"digest": "sha256:old"}, {"digest": "sha256:new"}])
    assert "2.0" in registry.find_file(img, DPKG_PATH)


def test_find_file_skips_a_layer_it_cannot_open(monkeypatch):
    blobs = {
        "sha256:good": tar_gz(DPKG_PATH, "Package: nginx\nVersion: 1.0\n"),
        "sha256:junk": b"this is not a gzipped tarball",
    }
    monkeypatch.setattr(registry, "get_blob", lambda repo, digest, token: blobs[digest])
    img = image([{"digest": "sha256:good"}, {"digest": "sha256:junk"}])
    assert "1.0" in registry.find_file(img, DPKG_PATH)


def test_find_file_returns_none_when_no_layer_has_the_path(monkeypatch):
    blobs = {"sha256:a": tar_gz("etc/hostname", "nope\n")}
    monkeypatch.setattr(registry, "get_blob", lambda repo, digest, token: blobs[digest])
    assert registry.find_file(image([{"digest": "sha256:a"}]), DPKG_PATH) is None
