"""Synthetic Jira DC contracts for S05's Windows handoff enhancements."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from jira_cli.cli import main
from jira_cli.config import UserError, load_config, set_config
from jira_cli.context import ContextStore, Selection
from jira_cli.metadata import find_field
from jira_cli.presentation import row_cells
from jira_cli.views import EPIC_NAME_SCHEMA, EPIC_SCHEMA

pytestmark = pytest.mark.usefixtures("application")
type Network = tuple[dict[str, Any], list[httpx.Request]]
LINK = "customfield_98761"
NAME = "customfield_98762"


def issue(key: str, kind: str = "Story", epic: str | None = None) -> dict[str, Any]:
    return {
        "key": key,
        "fields": {
            "issuetype": {"name": kind},
            "summary": "Different summary",
            "status": {"name": "Custom status", "statusCategory": {"key": "new"}},
            "priority": {"name": "Custom priority", "iconUrl": "HIDDEN_ICON"},
            "assignee": {"name": "fixture-user", "displayName": "Smith, Jane"},
            LINK: epic,
            NAME: "Data Quality" if kind == "Epic" else None,
        },
    }


@pytest.fixture
def ux(network: Network) -> Network:
    routes, _ = network
    routes["field"] = [
        {"id": LINK, "schema": {"custom": EPIC_SCHEMA}},
        {"id": NAME, "schema": {"custom": EPIC_NAME_SCHEMA}},
    ]
    routes["issue/WWRAP-10"] = issue("WWRAP-10", epic="WWRAP-100")
    routes["issue/WWRAP-100"] = issue("WWRAP-100", "Epic")

    def search(request: httpx.Request) -> httpx.Response:
        jql = request.url.params["jql"]
        rows = (
            [issue("WWRAP-100", "Epic")]
            if 'issuetype = "Epic"' in jql
            else [issue("WWRAP-10", epic="WWRAP-100"), issue("WWRAP-11")]
        )
        return httpx.Response(
            200, json={"startAt": 0, "total": len(rows), "issues": rows}
        )

    routes["search"] = search
    return network


def invoke(args: list[str], capsys: pytest.CaptureFixture[str]) -> Any:
    assert main([*args, "--format", "json"]) == 0
    return json.loads(capsys.readouterr().out)


def test_story_enrichment_batches_and_preserves_no_epic(
    ux: Network, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = invoke(["story", "list"], capsys)
    assert [(r["epic_key"], r["epic_name"]) for r in rows] == [
        ("WWRAP-100", "Data Quality"),
        (None, None),
    ]
    searches = [r for r in ux[1] if r.url.path.endswith("/search")]
    assert len(searches) == 2
    assert searches[1].url.params["jql"].startswith("key IN (WWRAP-100)")
    assert NAME in searches[0].url.params["fields"]
    assert len([r for r in ux[1] if r.url.path.endswith("/field")]) == 1


def test_context_reads_changed_epic_without_persisting_content(
    ux: Network, application: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    saved = invoke(["context", "set", "WWRAP-10"], capsys)
    assert saved["epic_name"] == "Data Quality"
    assert saved["project"] == "WWRAP"
    before = (application / "state.json").read_bytes()
    assert b"Data Quality" not in before
    assert b"WWRAP-100" not in before
    ux[0]["issue/WWRAP-10"]["fields"][LINK] = None
    shown = invoke(["context", "show"], capsys)
    assert shown["epic_key"] is None
    assert shown["epic_name"] is None
    assert shown["summary"] == "Different summary"
    assert (application / "state.json").read_bytes() == before
    ux[0]["issue/WWRAP-10"] = httpx.ConnectError("offline")
    assert "unavailable" in invoke(["context", "show"], capsys)["warning"]
    assert (application / "state.json").read_bytes() == before


@pytest.mark.usefixtures("ux")
def test_context_global_precedence_and_offline_set(
    application: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = ContextStore(application, load_config(application))
    store.update(Selection(issue="WWRAP-100"), global_scope=True)
    store.update(Selection(issue="WWRAP-10"))
    assert invoke(["context", "show"], capsys)["issue"] == "WWRAP-10"
    assert invoke(["context", "show", "--global"], capsys)["issue"] == "WWRAP-100"
    (application / ".env").unlink()
    assert "warning" in invoke(["context", "set", "WWRAP-10"], capsys)
    assert store.selection().issue == "WWRAP-10"


@pytest.mark.usefixtures("ux")
def test_epic_columns_and_metadata(capsys: pytest.CaptureFixture[str]) -> None:
    rows = invoke(["epic", "list"], capsys)["epics"]
    assert row_cells(rows[0], epics=True) == [
        "WWRAP-100",
        "Data Quality",
        "Different summary",
        "Custom status",
        "Custom priority",
        "Jane Smith",
        "1",
    ]
    assert row_cells(
        invoke(["story", "list"], capsys)[0], epics=False, stories=True
    ) == [
        "WWRAP-10",
        "Different summary",
        "WWRAP-100",
        "Data Quality",
        "Custom status",
        "Custom priority",
        "Jane Smith",
    ]


def comment(number: int) -> dict[str, Any]:
    return {
        "id": str(number),
        "body": f"Comment {number}\nSecond line",
        "created": f"2026-10-{number:02d}T10:30:00.000+0000",
        "author": {"displayName": "Smith, Jane"},
    }


def details(row: dict[str, Any]) -> None:
    row["fields"].update(
        {
            "description": "Full description\nMore detail",
            "comment": {
                "comments": [comment(25), comment(27), comment(26)],
                "total": 3,
            },
            "attachment": [
                {"filename": "validation.csv", "content": "HIDDEN_URL"},
                {"filename": "evidence.pdf"},
            ],
            "issuelinks": [
                {"outwardIssue": {"key": "WWRAP-20"}, "type": {"outward": "blocks"}},
                {
                    "inwardIssue": {"key": "WWRAP-21"},
                    "type": {"inward": "is blocked by"},
                },
            ],
            "created": "2026-10-24T10:30:00.000+0000",
            "updated": "2026-10-27T10:30:00.000+0000",
        }
    )


@pytest.mark.parametrize(
    ("command", "key"), [("story", "WWRAP-10"), ("epic", "WWRAP-100")]
)
def test_consistent_views_latest_and_detail(
    ux: Network,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    key: str,
) -> None:
    monkeypatch.setenv("COLUMNS", "110")
    details(ux[0]["issue/" + key])
    default = invoke([command, "view", key], capsys)
    assert default["latest_comment"]["id"] == "27"
    assert "comments" not in default
    detailed = invoke([command, "view", key, "--detail"], capsys)
    assert [r["id"] for r in detailed["comments"]] == ["27", "26", "25"]
    assert detailed["comments"][0]["author"] == {"name": "Jane Smith"}
    assert detailed["related_issues"][1] == {
        "key": "WWRAP-21",
        "relationship": "is blocked by",
    }
    assert main([command, "view", key]) == 0
    output = capsys.readouterr().out
    assert "Field" in output
    assert "Value" in output
    assert "Comment 27" in output
    assert "Comment 26" not in output
    assert ("Full description" in output) == (command == "story")
    assert "HIDDEN" not in output
    assert main([command, "view", key, "--detail"]) == 0
    output = capsys.readouterr().out
    assert output.count("Description") == 1
    assert output.index("Comment 27", output.index("Author")) < output.index(
        "Comment 26"
    )
    assert "Second line" in output
    assert "\\n" not in output
    assert "validation.csv, evidence.pdf" in output
    assert "WWRAP-20, WWRAP-21" in output
    assert "2026-10-24 10:30" in output
    assert "HIDDEN" not in output
    if command == "epic":
        assert "Child Stories (mine)" in output


@pytest.mark.parametrize(
    "description", [None, "", "First description line\nSecond line"]
)
def test_story_default_description(
    ux: Network,
    capsys: pytest.CaptureFixture[str],
    description: str | None,
) -> None:
    ux[0]["issue/WWRAP-10"]["fields"]["description"] = description
    assert main(["story", "view", "WWRAP-10"]) == 0
    output = capsys.readouterr().out
    assert output.count("Description") == 1
    if description:
        assert "First description line" in output
        assert "Second line" in output
        assert "\\n" not in output
    else:
        line = next(line for line in output.splitlines() if "Description" in line)
        assert "—" in line
    assert len([r for r in ux[1] if r.url.path.endswith("/issue/WWRAP-10")]) == 1
    result = invoke(["story", "view", "WWRAP-10"], capsys)
    assert result["description"] == description


def test_paged_comments_limit_and_latest(
    ux: Network, application: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ux[0]["issue/WWRAP-10"]["fields"]["comment"] = {
        "total": 3,
        "comments": [comment(25)],
    }

    def page(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params["startAt"])
        assert request.url.params["orderBy"] == "-created"
        return httpx.Response(
            200, json={"startAt": start, "total": 3, "comments": [comment(27 - start)]}
        )

    ux[0]["issue/WWRAP-10/comment"] = page
    assert invoke(["story", "view", "WWRAP-10"], capsys)["latest_comment"]["id"] == "27"
    assert len([r for r in ux[1] if r.url.path.endswith("/comment")]) == 1
    ux[1].clear()
    set_config(application, "output.comment_limit", "2")
    result = invoke(["story", "view", "WWRAP-10", "--detail"], capsys)
    assert [r["id"] for r in result["comments"]] == ["27", "26"]
    assert len([r for r in ux[1] if r.url.path.endswith("/comment")]) == 2


@pytest.mark.parametrize("comments", [None, {"comments": None}, {"comments": []}])
def test_missing_comments_and_details(
    ux: Network, capsys: pytest.CaptureFixture[str], comments: Any
) -> None:
    ux[0]["issue/WWRAP-10"]["fields"].update(
        comment=comments, attachment=None, issuelinks=None
    )
    result = invoke(["issue", "view", "WWRAP-10", "--detail"], capsys)
    assert result["latest_comment"] is None
    assert result["comments"] == result["attachments"] == result["related_issues"] == []
    assert main(["issue", "view", "WWRAP-10", "--detail"]) == 0
    assert "Comments: —" in capsys.readouterr().out


@pytest.mark.parametrize("bad", [[], {"comments": [None]}, {"comments": "invalid"}])
def test_invalid_comments_fail_safely(ux: Network, bad: Any) -> None:
    ux[0]["issue/WWRAP-10"]["fields"]["comment"] = bad
    assert main(["story", "view", "WWRAP-10", "--detail"]) == 1


@pytest.mark.parametrize(
    "metadata",
    [
        [{"id": NAME, "schema": {"custom": EPIC_NAME_SCHEMA}}] * 2,
        {"invalid": "metadata"},
    ],
)
def test_bad_epic_name_metadata(ux: Network, metadata: Any) -> None:
    ux[0]["field"] = metadata
    assert main(["story", "list"]) == 1


def test_creation_epic_name_schema_resolution() -> None:
    specs = {NAME: {"name": "Localized label", "schema": {"custom": EPIC_NAME_SCHEMA}}}
    assert find_field("epic_name", specs) == NAME
    with pytest.raises(UserError, match="ambiguous"):
        find_field("epic_name", {**specs, "customfield_999": specs[NAME]})


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(403),
        {"startAt": 0, "total": 2, "comments": []},
        {"startAt": 7, "total": 2, "comments": [comment(27)]},
        {"startAt": 0, "total": "2", "comments": [comment(27)]},
    ],
)
def test_comment_page_errors_do_not_change_context(
    ux: Network,
    application: Path,
    response: Any,
) -> None:
    store = ContextStore(application, load_config(application))
    store.update(Selection(issue="WWRAP-10"))
    before = (application / "state.json").read_bytes()
    ux[0]["issue/WWRAP-10"]["fields"]["comment"] = {"total": 2, "comments": []}
    ux[0]["issue/WWRAP-10/comment"] = response
    assert main(["story", "view", "--detail"]) == 1
    assert (application / "state.json").read_bytes() == before


def test_many_parent_epics_use_bounded_batches(
    ux: Network,
    capsys: pytest.CaptureFixture[str],
) -> None:
    stories = [
        issue(f"WWRAP-{number}", epic=f"EPIC-{number}") for number in range(1, 206)
    ]
    stories.append(issue("WWRAP-206", epic="EPIC-1"))
    batches: list[str] = []

    def search(request: httpx.Request) -> httpx.Response:
        jql = request.url.params["jql"]
        if jql.startswith("key IN"):
            batches.append(jql)
            keys = jql.split("(", 1)[1].split(")", 1)[0].split(",")
            # An inaccessible Epic is omitted by Jira rather than guessed.
            rows = [issue(key, "Epic") for key in keys if key != "EPIC-2"]
            total = len(rows)
        else:
            start = int(request.url.params["startAt"])
            rows = stories[start : start + 100]
            total = len(stories)
        return httpx.Response(
            200,
            json={
                "startAt": int(request.url.params["startAt"]),
                "total": total,
                "issues": rows,
            },
        )

    ux[0]["search"] = search
    result = invoke(["story", "list"], capsys)
    assert len(result) == 206
    assert len(batches) == 3
    assert result[1]["epic_key"] == "EPIC-2"
    assert result[1]["epic_name"] is None
    assert result[-1]["epic_name"] == "Data Quality"
    assert not any("/issue/" in r.url.path for r in ux[1])


@pytest.mark.parametrize("mode", ["argument", "yaml", "interactive"])
def test_epic_creation_distinct_name_summary(
    ux: Network,
    application: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    routes, requests = ux
    routes["mypermissions"] = {
        "permissions": {"CREATE_ISSUES": {"havePermission": True}}
    }
    routes["issue/createmeta/WWRAP/issuetypes"] = {
        "values": [{"id": "7", "name": "Epic"}],
        "isLast": True,
    }
    routes["issue/createmeta/WWRAP/issuetypes/7"] = {
        "isLast": True,
        "values": [
            {"fieldId": "summary", "required": True, "schema": {"type": "string"}},
            {
                "fieldId": NAME,
                "name": "Epic Name",
                "required": True,
                "schema": {"type": "string", "custom": EPIC_NAME_SCHEMA},
            },
        ],
    }
    routes["POST issue"] = httpx.Response(201, json={"id": "7", "key": "WWRAP-700"})
    args = ["create", "epic", "--summary", "Different summary"]
    if mode == "argument":
        args.extend(["--epic-name", "Data Quality"])
    elif mode == "yaml":
        definition = tmp_path / "epic.yaml"
        definition.write_text("epic_name: Data Quality\n")
        args.extend(["--from", str(definition)])
    else:
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("builtins.input", lambda: "Data Quality")
    assert main(args) == 0
    sent = json.loads(next(r.content for r in requests if r.method == "POST"))
    assert sent["fields"][NAME] == "Data Quality"
    assert sent["fields"]["summary"] == "Different summary"
    assert (
        ContextStore(application, load_config(application)).selection().issue
        == "WWRAP-700"
    )
    requests.clear()
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert (
        main(["create", "epic", "--summary", "Missing name", "--non-interactive"]) == 1
    )
    assert all(r.method == "GET" for r in requests)
    requests.clear()
    routes["issue/createmeta/WWRAP/issuetypes/7"]["values"].pop()
    assert (
        main(["create", "epic", "--epic-name", "Unsupported", "--summary", "Test"]) == 1
    )
    assert all(r.method == "GET" for r in requests)
