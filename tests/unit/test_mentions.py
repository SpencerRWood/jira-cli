import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from jira_cli.cli import main

pytestmark = pytest.mark.usefixtures("application")
type Network = tuple[dict[str, Any], list[httpx.Request]]


@pytest.fixture
def mentions(network: Network) -> Network:
    routes, _ = network
    routes["mypermissions"] = {
        "permissions": {"ADD_COMMENTS": {"havePermission": True}}
    }

    def search(request: httpx.Request) -> httpx.Response:
        name = request.url.params["username"]
        return httpx.Response(200, json=[{"name": name, "key": name, "active": True}])

    routes["user/search"] = search
    routes["POST issue/WWRAP-1/comment"] = httpx.Response(201, json={"id": "55"})
    return network


@pytest.mark.parametrize(
    ("body", "args", "expected", "lookups"),
    [
        ("Review @jsmith", [], "Review [~jsmith]", 1),
        ("Review", ["--mention", "jsmith"], "Review\n[~jsmith]", 1),
        ("Review @jsmith and @adoe", [], "Review [~jsmith] and [~adoe]", 2),
        ("@jsmith @JSMITH.", ["--mention", "jsmith"], "[~jsmith] [~jsmith].", 1),
        (
            "a@jsmith.test @ ordinary foo/@jsmith",
            [],
            "a@jsmith.test @ ordinary foo/@jsmith",
            0,
        ),
        (
            "`@jsmith` {code}@adoe{code} {{@abc}}",
            [],
            "`@jsmith` {code}@adoe{code} {{@abc}}",
            0,
        ),
        ("```python\n@jsmith\n```", [], "```python\n@jsmith\n```", 0),
        ("[~jsmith]", [], "[~jsmith]", 0),
        ("[~jsmith]", ["--mention", "jsmith"], "[~jsmith]", 1),
        ("ordinary text\n* unchanged &amp;", [], "ordinary text\n* unchanged &amp;", 0),
        ("{code}@jsmith", [], "{code}@jsmith", 0),
        ("```python\n@jsmith", [], "```python\n@jsmith", 0),
    ],
)
def test_mentions(  # noqa: PLR0913, PLR0917
    mentions: Network,
    capsys: pytest.CaptureFixture[str],
    body: str,
    args: list[str],
    expected: str,
    lookups: int,
) -> None:
    assert (
        main(["comment", "add", body, "--issue", "WWRAP-1", "--format", "json", *args])
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    requests = mentions[1]
    assert json.loads(requests[-1].content) == {"body": expected}
    assert len([r for r in requests if r.url.path.endswith("user/search")]) == lookups
    if lookups:
        assert result["notification_delivery"] == "unverified"
        assert result["comment_created"] is True
        assert result["mention_markup_submitted"] is True
    else:
        assert result == {"key": "WWRAP-1", "comment_id": "55"}


@pytest.mark.parametrize(
    ("users", "reason"),
    [
        ([], "unknown"),
        (
            [
                {"name": "jsmith", "active": True, "key": "a"},
                {"name": "JSMITH", "active": True, "key": "b"},
            ],
            "ambiguous",
        ),
        (httpx.Response(403), "permission denied"),
        ({"invalid": True}, "invalid user search"),
    ],
)
def test_lookup_failure_prevents_write(
    mentions: Network, capsys: pytest.CaptureFixture[str], users: Any, reason: str
) -> None:
    mentions[0]["user/search"] = users
    assert main(["comment", "add", "Original @jsmith", "--issue", "WWRAP-1"]) == 1
    assert reason in capsys.readouterr().err
    assert not any(r.method == "POST" for r in mentions[1])


def test_file_comment(mentions: Network, tmp_path: Path) -> None:
    path = tmp_path / "comment.txt"
    path.write_text("Review\n@jsmith", encoding="utf-8")
    assert main(["comment", "add", "--file", str(path), "--issue", "WWRAP-1"]) == 0
    assert json.loads(mentions[1][-1].content)["body"] == "Review\n[~jsmith]"
    assert path.read_text(encoding="utf-8") == "Review\n@jsmith"


def test_submission_failure(
    mentions: Network, capsys: pytest.CaptureFixture[str]
) -> None:
    mentions[0]["POST issue/WWRAP-1/comment"] = httpx.Response(403)
    assert main(["comment", "add", "Review @jsmith", "--issue", "WWRAP-1"]) == 1
    output = capsys.readouterr()
    assert "rejected the write" in output.err
    assert "comment_created" not in output.out
    assert len([r for r in mentions[1] if r.method == "POST"]) == 1


def test_later_unknown_reference_prevents_partial_submission(
    mentions: Network, capsys: pytest.CaptureFixture[str]
) -> None:
    def search(request: httpx.Request) -> httpx.Response:
        name = request.url.params["username"]
        users = [{"name": name, "active": True}] if name == "jsmith" else []
        return httpx.Response(200, json=users)

    mentions[0]["user/search"] = search
    assert (
        main(["comment", "add", "Review @jsmith and @unknown", "--issue", "WWRAP-1"])
        == 1
    )
    assert "@unknown" in capsys.readouterr().err
    assert not any(request.method == "POST" for request in mentions[1])


def test_repeated_lookup_page_fails_closed(
    mentions: Network, capsys: pytest.CaptureFixture[str]
) -> None:
    mentions[0]["user/search"] = [
        {"name": f"jsmith{number}", "active": True} for number in range(100)
    ]
    assert main(["comment", "add", "Review @jsmith", "--issue", "WWRAP-1"]) == 1
    assert "pagination repeated" in capsys.readouterr().err
    assert not any(request.method == "POST" for request in mentions[1])
