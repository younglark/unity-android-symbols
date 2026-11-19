import json
from types import SimpleNamespace

from unity_symbols.publish import ReleaseStore


class FakeRunner:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        return self.responses.pop(0)


def ok(stdout=""):
    return SimpleNamespace(returncode=0, stdout=stdout, stderr="")


def not_found():
    return SimpleNamespace(returncode=1, stdout="", stderr="Not Found")


def release(*, draft, assets=None, tag="tag"):
    return ok(json.dumps({"tag_name": tag, "draft": draft, "assets": assets or []}))


def listing(*releases):
    return ok(json.dumps(list(releases)))


def test_get_reads_drafts_through_the_list_endpoint():
    runner = FakeRunner(
        [
            not_found(),
            listing({"tag_name": "tag", "draft": True, "assets": []}),
        ]
    )
    store = ReleaseStore("owner/repo", runner=runner)

    result = store.get("tag")

    assert result["draft"] is True
    assert runner.calls[1][0][2].startswith("repos/owner/repo/releases?per_page=100")


def test_is_complete_requires_published_assets_with_size():
    runner = FakeRunner(
        [
            release(draft=True),
            release(draft=False, assets=[{"name": "a", "size": 1}, {"name": "b", "size": 0}]),
            release(draft=False, assets=[{"name": "a", "size": 1}, {"name": "b", "size": 2}]),
            not_found(),
            listing(),
        ]
    )
    store = ReleaseStore("owner/repo", runner=runner)

    assert store.is_complete("tag", ["a", "b"]) is False  # draft
    assert store.is_complete("tag", ["a", "b"]) is False  # zero-sized asset
    assert store.is_complete("tag", ["a", "b"]) is True
    assert store.is_complete("tag", ["a", "b"]) is False  # no release at all


def test_ensure_draft_creates_only_when_missing():
    runner = FakeRunner(
        [
            not_found(),
            listing(),
            ok(),  # create
            release(draft=True),
        ]
    )
    store = ReleaseStore("owner/repo", runner=runner)

    created = store.ensure_draft("tag", "Title")

    assert created["draft"] is True
    commands = [command for command, _ in runner.calls]
    assert commands[2][:3] == ["gh", "release", "create"]

    runner = FakeRunner([release(draft=True)])
    store = ReleaseStore("owner/repo", runner=runner)

    existing = store.ensure_draft("tag", "Title")

    assert existing["draft"] is True
    assert all(command[:3] != ["gh", "release", "create"] for command, _ in runner.calls)


def test_publish_flow_creates_draft_then_uploads_then_publishes(tmp_path):
    first = tmp_path / "libunity.aaa.arm64-v8a.so"
    second = tmp_path / "symbols.aaa.json"
    first.write_bytes(b"lib")
    second.write_text("{}")

    runner = FakeRunner([ok(), ok(), ok()])
    store = ReleaseStore("owner/repo", runner=runner)

    store.create_draft("unity-1.0.0-aaa", "Unity 1.0.0 (aaa)")
    store.upload("unity-1.0.0-aaa", [first, second])
    store.publish("unity-1.0.0-aaa")

    commands = [command for command, _ in runner.calls]
    assert commands[0][:3] == ["gh", "release", "create"]
    assert commands[0][3] == "unity-1.0.0-aaa"
    assert "--draft" in commands[0]
    assert commands[1][:3] == ["gh", "release", "upload"]
    assert str(first) in commands[1] and str(second) in commands[1]
    assert "--clobber" in commands[1]
    assert commands[2] == ["gh", "release", "edit", "unity-1.0.0-aaa", "--draft=false"]
