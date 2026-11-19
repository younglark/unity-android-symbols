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


def release_json(*, draft, assets):
    return ok(json.dumps({"draft": draft, "assets": assets}))


def test_is_complete_requires_published_assets_with_size():
    runner = FakeRunner(
        [
            release_json(draft=True, assets=[{"name": "a", "size": 1}]),
            release_json(draft=False, assets=[{"name": "a", "size": 1}, {"name": "b", "size": 0}]),
            release_json(draft=False, assets=[{"name": "a", "size": 1}, {"name": "b", "size": 2}]),
            not_found(),
        ]
    )
    store = ReleaseStore("owner/repo", runner=runner)

    assert store.is_complete("tag", ["a", "b"]) is False  # draft
    assert store.is_complete("tag", ["a", "b"]) is False  # zero-sized asset
    assert store.is_complete("tag", ["a", "b"]) is True
    assert store.is_complete("tag", ["a", "b"]) is False  # missing release


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
