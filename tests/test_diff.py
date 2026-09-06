from ocidiff import diff
from ocidiff.diff import Change
from ocidiff.registry import Image

DPKG_STATUS = """\
Package: nginx
Status: install ok installed
Version: 1.27.0-2~bookworm

Package: openssl
Version: 3.0.13-1~deb12u1

Package: broken-no-version
Status: install ok installed

Package: tar
Version: 1.34+dfsg-1.2
"""


def image(manifest=None, config=None) -> Image:
    return Image(
        repo="library/x",
        tag="1",
        manifest=manifest or {},
        config=config or {},
        token="",
    )


def test_size_mib_sums_the_compressed_layer_sizes():
    img = image(manifest={"layers": [{"size": 1048576}, {"size": 2097152}]})
    assert diff.size_mib(img) == 3.0


def test_layer_count():
    assert diff.layer_count(image(manifest={"layers": [{}, {}, {}]})) == 3


def test_read_env_splits_on_the_first_equals_only():
    img = image(config={"config": {"Env": ["PATH=/usr/bin", "OPTS=-Dfile.encoding=UTF-8"]}})
    assert diff.read_env(img) == {"PATH": "/usr/bin", "OPTS": "-Dfile.encoding=UTF-8"}


def test_read_env_ignores_lines_without_an_equals():
    img = image(config={"config": {"Env": ["BROKEN", "OK=1"]}})
    assert diff.read_env(img) == {"OK": "1"}


def test_read_env_when_the_image_declares_none():
    assert diff.read_env(image()) == {}


def test_parse_dpkg_status_reads_name_and_version_pairs():
    assert diff.parse_dpkg_status(DPKG_STATUS) == {
        "nginx": "1.27.0-2~bookworm",
        "openssl": "3.0.13-1~deb12u1",
        "tar": "1.34+dfsg-1.2",
    }


def test_parse_dpkg_status_skips_a_stanza_with_no_version():
    assert "broken-no-version" not in diff.parse_dpkg_status(DPKG_STATUS)


def test_parse_dpkg_status_ignores_a_version_with_no_package():
    assert diff.parse_dpkg_status("Version: 1.0\n") == {}


def test_diff_dicts_classifies_every_kind_and_sorts_by_name():
    a = {"same": "1", "changed": "1", "gone": "1"}
    b = {"same": "1", "changed": "2", "new": "1"}
    assert diff.diff_dicts(a, b) == [
        Change("~", "changed", "1", "2"),
        Change("-", "gone", "1", None),
        Change("+", "new", None, "1"),
    ]


def test_diff_dicts_is_empty_when_nothing_moved():
    assert diff.diff_dicts({"a": "1"}, {"a": "1"}) == []


def test_compare_builds_the_cheap_half_and_leaves_packages_unset():
    a = image(manifest={"layers": [{"size": 1048576}]}, config={"config": {"Env": ["V=1"]}})
    b = image(manifest={"layers": [{"size": 1048576}]}, config={"config": {"Env": ["V=2"]}})
    report = diff.compare(a, b)
    assert report.env == [Change("~", "V", "1", "2")]
    assert report.layers_a == 1
    assert report.packages is None
    assert report.packages_note == ""


def test_compare_packages_diffs_both_sides(monkeypatch):
    sides = iter([{"nginx": "1.0"}, {"nginx": "2.0"}])
    monkeypatch.setattr(diff, "read_packages", lambda img: next(sides))
    assert diff.compare_packages(image(), image()) == [Change("~", "nginx", "1.0", "2.0")]


def test_compare_packages_is_none_when_either_side_is_not_debian(monkeypatch):
    sides = iter([{"nginx": "1.0"}, None])
    monkeypatch.setattr(diff, "read_packages", lambda img: next(sides))
    assert diff.compare_packages(image(), image()) is None
