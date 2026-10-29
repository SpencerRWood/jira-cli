import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from jira_cli import personal
from jira_cli.cli import completion, main, parser
from jira_cli.client import JiraClient
from jira_cli.completion import tree
from jira_cli.config import Config, UserError, load_config, set_config
from jira_cli.context import ContextStore, Selection
from jira_cli.personal_cli import select

pytestmark = pytest.mark.usefixtures("application")
LINK = "customfield_23456"
type Network = tuple[dict[str, Any], list[httpx.Request]]


def test_epic_include_done_only_broadens_assigned_rule(
    network: Network,
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup(
        network,
        [
            issue("WWRAP-1", kind="Epic"),
            issue("WWRAP-2", kind="Epic", category="done", assignee="other"),
            issue("WWRAP-3", kind="Epic", category="done"),
        ],
        [issue("OTHER-1", epic="WWRAP-2")],
    )
    result = invoke(["epic", "list"], capsys)
    assert [r["key"] for r in result["epics"]] == ["WWRAP-1", "WWRAP-2"]
    result = invoke(["epic", "list", "--mine", "--include-done"], capsys)
    assert [r["key"] for r in result["epics"]] == ["WWRAP-1", "WWRAP-3"]


def issue(
    key: str,
    *,
    kind: str = "Story",
    category: str = "new",
    assignee: str | None = "fixture-user",
    epic: str | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "fields": {
            "summary": "Summary " + key,
            "issuetype": {"name": kind},
            "status": {"name": category, "statusCategory": {"key": category}},
            "priority": {"name": "High"},
            "assignee": {"name": assignee} if assignee else None,
            LINK: epic,
        },
    }


def setup(
    network: Network,
    epics: list[dict[str, Any]],
    stories: list[dict[str, Any]],
) -> None:
    routes, _ = network
    routes["field"] = [{"id": LINK, "schema": {"custom": personal.EPIC_SCHEMA}}]

    def search(request: httpx.Request) -> httpx.Response:
        jql = request.url.params["jql"]
        rows = epics if 'issuetype = "Epic"' in jql else stories
        return httpx.Response(
            200, json={"startAt": 0, "total": len(rows), "issues": rows}
        )

    routes["search"] = search


@pytest.mark.parametrize("options", [[], ["--mine"]])
def test_done_category_epic_without_children_is_excluded(
    network: Network,
    capsys: pytest.CaptureFixture[str],
    options: list[str],
) -> None:
    unresolved = issue("WWRAP-1", kind="Epic", category="done")
    unresolved["fields"]["resolution"] = None
    resolved = issue("WWRAP-2", kind="Epic", category="done")
    resolved["fields"]["resolution"] = {"name": "Done"}
    other = issue("WWRAP-3", kind="Epic", category="done", assignee="other")
    other["fields"]["resolution"] = None
    setup(network, [unresolved, resolved, other, unresolved], [])
    result = invoke(["epic", "list", *options], capsys)
    assert result["epics"] == []
    query = next(
        r for r in network[1] if 'issuetype = "Epic"' in r.url.params.get("jql", "")
    )
    assert "resolution" in query.url.params["fields"]


def invoke(args: list[str], capsys: pytest.CaptureFixture[str]) -> Any:
    assert main([*args, "--format", "json"]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    return json.loads(output.out)


def test_relevant_or_dedup_and_completed_epic(
    network: Network,
    capsys: pytest.CaptureFixture[str],
    application: Path,
) -> None:
    epics = [
        issue("WWRAP-1", kind="Epic"),
        issue("WWRAP-2", kind="Epic", assignee="other"),
        issue("WWRAP-3", kind="Epic", category="done", assignee=None),
        issue("WWRAP-4", kind="Epic", category="done"),
        issue("WWRAP-5", kind="Epic", assignee="other"),
    ]
    stories = [
        issue("OTHER-1", epic="WWRAP-1"),
        issue("OTHER-2", epic="WWRAP-2"),
        issue("OTHER-3", epic="WWRAP-3"),
        issue("OTHER-4", epic="WWRAP-3"),
    ]
    setup(network, [*epics, epics[0]], [*stories, stories[0]])
    result = invoke(["epic", "list"], capsys)
    assert [row["key"] for row in result["epics"]] == ["WWRAP-1", "WWRAP-2", "WWRAP-3"]
    assert [row["my_active_story_count"] for row in result["epics"]] == [1, 1, 2]
    assert result["epics"][2]["completed_with_active_personal_stories"] is True
    assert not (application / "state.json").exists()
    assert all(r.method == "GET" for r in network[1])
    assert any("cf[23456]" in r.url.params.get("jql", "") for r in network[1])
    assert all(
        LINK in r.url.params["fields"]
        for r in network[1]
        if "statusCategory" in r.url.params.get("jql", "")
    )


@pytest.mark.parametrize(
    ("option", "expected"),
    [
        ("--mine", ["WWRAP-1"]),
        ("--with-my-stories", ["WWRAP-2"]),
        ("--all", ["WWRAP-1", "WWRAP-2", "WWRAP-3"]),
    ],
)
def test_epic_filter_modes(
    network: Network,
    capsys: pytest.CaptureFixture[str],
    option: str,
    expected: list[str],
) -> None:
    setup(
        network,
        [
            issue("WWRAP-1", kind="Epic"),
            issue("WWRAP-2", kind="Epic", category="done", assignee="other"),
            issue("WWRAP-3", kind="Epic", assignee="other"),
        ],
        [issue("WWRAP-9", epic="WWRAP-2")],
    )
    result = invoke(["epic", "list", option, "--project", "OTHER"], capsys)
    assert [row["key"] for row in result["epics"]] == expected
    assert any('project = "OTHER"' in r.url.params.get("jql", "") for r in network[1])


@pytest.mark.parametrize(
    "setting",
    [
        "include_assigned_open",
        "include_with_my_active_stories",
        "include_completed_epics_with_active_stories",
    ],
)
def test_relevance_config(
    network: Network,
    application: Path,
    capsys: pytest.CaptureFixture[str],
    setting: str,
) -> None:
    setup(
        network,
        [
            issue("WWRAP-1", kind="Epic"),
            issue("WWRAP-2", kind="Epic", category="done", assignee="other"),
        ],
        [issue("WWRAP-9", epic="WWRAP-2")],
    )
    set_config(application, "epics." + setting, "false")
    rows = invoke(["epic", "list"], capsys)["epics"]
    assert [r["key"] for r in rows] == (
        ["WWRAP-2"] if setting == "include_assigned_open" else ["WWRAP-1"]
    )


def test_epic_view_grouping_and_explicit_all_scope(
    network: Network,
    capsys: pytest.CaptureFixture[str],
    application: Path,
) -> None:
    epic = issue("WWRAP-1", kind="Epic", category="done")
    mine = [issue("WWRAP-2"), issue("WWRAP-3", category="done")]
    mine[1]["fields"]["priority"] = {"name": "Low"}
    other = issue("WWRAP-4", assignee="other")
    setup(network, [], [])
    network[0]["issue/WWRAP-1"] = epic

    def search(request: httpx.Request) -> httpx.Response:
        rows = mine if "currentUser()" in request.url.params["jql"] else [*mine, other]
        return httpx.Response(
            200, json={"startAt": 0, "total": len(rows), "issues": rows}
        )

    network[0]["search"] = search
    store = ContextStore(application, load_config(application))
    store.update(Selection(issue="WWRAP-1"))
    before = (application / "state.json").read_bytes()
    result = invoke(["epic", "view", "--mine"], capsys)
    assert result["my_active_story_count"] == 1
    assert result["completed_with_active_personal_stories"] is True
    assert [g["status"] for g in result["groups"]] == ["new", "done"]
    assert sum(len(g["stories"]) for g in result["groups"]) == 2
    result = invoke(["epic", "view", "--all-stories", "--issue", "WWRAP-1"], capsys)
    assert result["story_scope"] == "all"
    assert sum(len(g["stories"]) for g in result["groups"]) == 3
    assert result["my_active_story_count"] == 1
    assert (application / "state.json").read_bytes() == before


@pytest.mark.parametrize("sort", ["due-date", "priority", "status", "staleness"])
def test_today_across_projects_and_sort(
    network: Network,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    sort: str,
    application: Path,
) -> None:
    setup(network, [], [issue("OTHER-1"), issue("WWRAP-2")])
    monkeypatch.setenv("JIRA_PROJECT", "SAVED")
    ContextStore(application, load_config(application)).update(
        Selection(project="CONTEXT")
    )
    result = invoke(["my", "today", "--sort", sort], capsys)
    assert result["scope"] == "all-accessible-projects"
    assert len(result["issues"]) == 2
    request = network[1][-1]
    assert "project =" not in request.url.params["jql"]
    assert "resolution IS EMPTY" in request.url.params["jql"]
    assert "currentUser()" in request.url.params["jql"]
    assert 'issuetype = "Story"' in request.url.params["jql"]
    assert (
        "ORDER BY "
        + {
            "due-date": "duedate ASC",
            "priority": "priority DESC",
            "status": "status ASC",
            "staleness": "updated ASC",
        }[sort]
        in request.url.params["jql"]
    )
    assert "duedate,issuelinks" in request.url.params["fields"]
    result = invoke(["--project", "WWRAP", "my", "today"], capsys)
    assert result["scope"] == "WWRAP"
    assert 'project = "WWRAP"' in network[1][-1].url.params["jql"]


def test_blockers_and_labeled_risks() -> None:
    row = issue("WWRAP-1")
    row["fields"].update(
        {
            "duedate": "2026-10-01",
            "updated": "2026-10-01T08:00:00.000+0000",
            "issuelinks": [
                {"type": {"inward": "is blocked by"}, "inwardIssue": issue("OTHER-1")},
                {
                    "type": {"inward": "is blocked by"},
                    "inwardIssue": issue("OTHER-2", category="done"),
                },
                {
                    "type": {"inward": "is blocked by"},
                    "inwardIssue": {"key": "OTHER-3"},
                },
                {"type": {"inward": "relates to"}, "inwardIssue": issue("OTHER-4")},
                {"type": {"outward": "blocks"}, "outwardIssue": issue("OTHER-5")},
            ],
        }
    )
    result = personal.risks(row, datetime(2026, 10, 26, tzinfo=UTC), 14)
    assert result["blockers"] == ["OTHER-1"]
    assert len(result["inferred_risks"]) == 3
    assert "status is unavailable" in result["inferred_risks"][0]
    assert "not proof" in result["inferred_risks"][1]
    row["fields"].update(
        {
            "issuelinks": [],
            "duedate": "2026-11-01",
            "updated": "2026-10-25T08:00:00+00:00",
        }
    )
    assert (
        personal.risks(row, datetime(2026, 10, 26, tzinfo=UTC), 14)["inferred_risks"]
        == []
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("duedate", "not-date"),
        ("updated", "not-date"),
        ("updated", "2026-10-01T00:00:00"),
        ("issuelinks", "invalid"),
        ("issuelinks", [None]),
    ],
)
def test_invalid_risk_metadata(field: str, value: Any) -> None:
    row = issue("WWRAP-1")
    row["fields"][field] = value
    with pytest.raises(UserError, match="Jira returned"):
        personal.risks(row, datetime(2026, 10, 26, tzinfo=UTC), 14)


@pytest.mark.parametrize(
    "status",
    [
        None,
        "Done",
        {"statusCategory": None},
        {"statusCategory": {"key": []}},
        {"statusCategory": {"key": {}}},
    ],
)
def test_invalid_status_category_fails_without_guessing(status: Any) -> None:
    row = issue("WWRAP-1")
    row["fields"]["status"] = status
    with pytest.raises(UserError, match="status category"):
        personal.done(row)


@pytest.mark.parametrize("command", ["issue", "story", "epic"])
def test_explicit_picker_updates_only_local_state(
    network: Network,
    monkeypatch: pytest.MonkeyPatch,
    application: Path,
    capsys: pytest.CaptureFixture[str],
    command: str,
) -> None:
    row = issue("WWRAP-1", kind="Epic")
    row["fields"]["summary"] = "fixture-pat-never-print\x1b[31m\nunsafe"
    setup(network, [row], [row])
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda: "1")
    assert main([command, "select"]) == 0
    output = capsys.readouterr()
    assert "fixture-pat-never-print" not in output.err + output.out
    assert "\x1b" not in output.err
    assert "\\u001b" in output.err
    assert "Summary" in output.err
    assert "Status" in output.err
    assert "completed_with_active_personal_stories" not in output.err
    assert '"summary"' not in output.err
    assert ContextStore(application, Config()).selection().issue == "WWRAP-1"
    assert all(r.method == "GET" for r in network[1])


@pytest.mark.parametrize(
    "answer", ["", "0", "99", "garbage", "\u0661", "1.0", "1" * 5000]
)
def test_picker_cancel_or_invalid_keeps_context(
    network: Network,
    monkeypatch: pytest.MonkeyPatch,
    application: Path,
    answer: str,
) -> None:
    setup(network, [], [issue("WWRAP-1")])
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda: answer)
    store = ContextStore(application, Config())
    store.update(Selection(issue="WWRAP-99"))
    before = (application / "state.json").read_bytes()
    assert main(["issue", "select"]) == 1
    assert (application / "state.json").read_bytes() == before


@pytest.mark.parametrize(
    "flag", [[], ["--quiet"], ["--format", "json"], ["--non-interactive"]]
)
def test_picker_never_prompts_for_scripts(network: Network, flag: list[str]) -> None:
    assert main(["issue", "select", *flag]) == 1
    assert network[1] == []


def test_empty_disabled_and_interrupted_picker(
    network: Network,
    monkeypatch: pytest.MonkeyPatch,
    application: Path,
) -> None:
    setup(network, [], [])
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    assert main(["issue", "select"]) == 1
    setup(network, [], [issue("WWRAP-1")])
    set_config(application, "context.persist", "false")
    assert main(["issue", "select"]) == 1
    set_config(application, "context.persist", "true")

    def interrupted() -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", interrupted)
    assert main(["issue", "select"]) == 1
    assert not (application / "state.json").exists()


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        [None],
        [],
        [{"id": "bad", "schema": {"custom": personal.EPIC_SCHEMA}}],
        [{"id": LINK, "schema": {"custom": personal.EPIC_SCHEMA}}] * 2,
        [{"id": LINK, "schema": "invalid"}],
    ],
)
def test_no_guessed_epic_field(network: Network, metadata: Any) -> None:
    network[0]["field"] = metadata
    assert main(["epic", "list"]) == 1
    assert not any(r.url.path.endswith("search") for r in network[1])


def test_invalid_epic_identity_status_and_story_shapes(network: Network) -> None:
    setup(network, [issue("WWRAP-1", kind="Epic")], [])
    network[0]["myself"] = {}
    assert main(["epic", "list"]) == 1
    network[0]["myself"] = {"name": "fixture-user"}
    setup(network, [issue("WWRAP-1", category="unknown")], [])
    assert main(["epic", "list"]) == 1
    setup(network, [{"key": "WWRAP-1"}], [])
    assert main(["epic", "list"]) == 1
    assert main(["epic", "view", "WWRAP-1"]) == 1
    network[0]["issue/WWRAP-1"] = issue("WWRAP-1", kind="Epic")
    setup(network, [], [issue("WWRAP-2", category="done")])
    network[0]["search"] = lambda _: httpx.Response(
        200,
        json={"startAt": 0, "total": 1, "issues": [issue("WWRAP-2", category="done")]},
    )
    assert main(["epic", "view", "WWRAP-1", "--format", "json"]) == 0


def test_completion_from_parser_is_local_and_contextual(
    network: Network,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    application: Path,
) -> None:
    (application / ".env").unlink()
    (application / "config.yaml").write_text("not: valid")
    monkeypatch.delenv("JIRA_PAT", raising=False)
    assert main(["completion", "powershell"]) == 0
    assert "Register-ArgumentCompleter" in capsys.readouterr().out
    assert network[1] == []
    spec = tree(parser())
    assert set(spec["children"]["epic"]["children"]) == {"list", "view", "select"}
    assert spec["children"]["my"]["children"]["today"]["options"]["--sort"][
        "choices"
    ] == ["due-date", "priority", "status", "staleness"]
    assert "StartsWith" in completion()


def test_picker_redacts_escaped_pat(
    application: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    token = 'quote"secret\\pat'  # noqa: S105 - synthetic redaction fixture
    row = issue("WWRAP-1")
    row["fields"]["summary"] = token + "\x1b[31m"
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda: "1")
    args = parser().parse_args(
        ["issue", "select", "--format", "table"],
        namespace=argparse.Namespace(quiet=False),
    )
    client = JiraClient(load_config(application), SecretStr(token))
    try:
        select([row], args, client, ContextStore(application, Config()))
    finally:
        client.close()
    output = capsys.readouterr().err
    assert json.dumps(token)[1:-1] not in output
    assert "[REDACTED]" in output
    assert "\x1b" not in output


def test_today_does_not_read_saved_issue_context(
    network: Network,
    application: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup(network, [], [issue("WWRAP-1")])
    (application / "state.json").write_text("not-json")
    assert invoke(["my", "today"], capsys)["scope"] == "all-accessible-projects"


@pytest.mark.parametrize("options", [[], ["--format", "table"]])
def test_today_renders_issue_rows_with_attention(
    network: Network,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    options: list[str],
) -> None:
    monkeypatch.setenv("COLUMNS", "220")
    first = issue("WWRAP-10")
    first["fields"].update(
        summary="Review fixture validation",
        duedate="2000-01-01",
        updated="2000-01-01T10:30:00+00:00",
        issuelinks=[
            {"type": {"inward": "is blocked by"}, "inwardIssue": issue("OTHER-20")}
        ],
    )
    first["fields"]["status"]["name"] = "Review"
    setup(network, [], [first, issue("WWRAP-11")])
    args = ["my", "today", "--project", "WWRAP", "--sort", "priority"]
    assert main([*args, *options]) == 0
    output = capsys.readouterr().out
    assert "Scope: WWRAP  Sort: priority" in output
    assert "MY TODAY — TOP 5 PRIORITIES" in output
    assert "Showing 1 of 1 actionable stories" in output
    assert "STORIES NEEDING REVIEW" in output
    labels = [
        "Key",
        "Summary",
        "Status",
        "Priority",
        "Due Date",
        "Updated",
        "Latest Comment",
        "Attention",
    ]
    positions = [output.index(label) for label in labels]
    assert positions == sorted(positions)
    assert output.index("WWRAP-11") < output.index("WWRAP-10")
    assert "Review fixture validation" in output
    assert "—" in output
    assert '"issues"' not in output
    assert '"name"' not in output
    assert len(network[1]) == 1
    query = network[1][0].url.params["jql"]
    assert 'project = "WWRAP"' in query
    assert "ORDER BY priority DESC, key ASC" in query
    result = invoke(args, capsys)
    assert result["review"][0]["blockers"] == ["OTHER-20"]
    assert result["issues"][0]["due_date"] is None
    assert main([*args, "--quiet"]) == 0
    assert capsys.readouterr().out == ""


def test_today_empty_table(
    network: Network, capsys: pytest.CaptureFixture[str]
) -> None:
    setup(network, [], [])
    assert main(["my", "today"]) == 0
    output = capsys.readouterr().out
    assert "Scope: all-accessible-projects  Sort: urgency" in output
    assert "No matching issues." in output
    assert invoke(["my", "today"], capsys)["issues"] == []


def test_today_defaults_to_urgency_and_excludes_non_stories(
    network: Network, capsys: pytest.CaptureFixture[str]
) -> None:
    older = issue("OTHER-10", kind="Bug")
    older["fields"]["updated"] = "2026-10-01T10:30:00+00:00"
    newer = issue("WWRAP-11")
    newer["fields"]["updated"] = "2026-10-27T10:30:00+00:00"

    def search(request: httpx.Request) -> httpx.Response:
        assert request.url.params["jql"] == (
            'assignee = currentUser() AND resolution IS EMPTY AND issuetype = "Story" '
            "AND statusCategory != Done ORDER BY priority DESC, key ASC"
        )
        return httpx.Response(
            200, json={"startAt": 0, "total": 2, "issues": [older, newer]}
        )

    network[0]["search"] = search
    result = invoke(["my", "today"], capsys)
    assert result["sort"] == "urgency"
    assert [row["key"] for row in result["issues"]] == ["WWRAP-11"]
    assert [row["type"] for row in result["issues"]] == ["Story"]
    assert len(network[1]) == 1
    assert main(["my", "today"]) == 0
    output = capsys.readouterr().out
    assert "Sort: urgency" in output
    assert "OTHER-10" not in output
    assert "WWRAP-11" in output


def test_today_table_escapes_controls_and_redacts_pat(
    network: Network,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLUMNS", "80")
    row = issue("WWRAP-10")
    row["fields"]["summary"] = "fixture-pat-never-print\x1b[31m\nFixture summary"
    row["fields"]["priority"]["iconUrl"] = "HIDDEN_ICON"
    row["fields"]["comment"] = {
        "comments": [
            {
                "body": "fixture-pat-never-print\x1b[31m",
                "author": {"displayName": "Smith, Jane"},
            }
        ]
    }
    setup(network, [], [row])
    assert main(["my", "today"]) == 0
    output = capsys.readouterr().out
    assert "fixture-pat-never-print" not in output
    assert "\x1b" not in output
    assert "HIDDEN_ICON" not in output
    assert 'summary"' not in output
    result = invoke(["my", "today"], capsys)
    assert result["issues"][0]["summary"].startswith("[REDACTED]")
    assert result["issues"][0]["latest_comment"]["body"].startswith("[REDACTED]")


def test_today_latest_comment_uses_search_data(
    network: Network,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLUMNS", "260")
    row = issue("WWRAP-10")
    row["fields"]["comment"] = {
        "total": 2,
        "comments": [
            {
                "id": "1",
                "body": "Older comment",
                "created": "2026-10-26T10:30:00+00:00",
            },
            {
                "id": "2",
                "body": "Latest fixture comment\nSecond fixture line",
                "created": "2026-10-27T10:30:00+00:00",
                "author": {
                    "displayName": "Smith, Jane",
                    "avatarUrls": {"48": "HIDDEN_AVATAR"},
                },
            },
        ],
    }
    setup(network, [], [row, issue("WWRAP-11")])
    assert main(["my", "today"]) == 0
    output = capsys.readouterr().out
    assert "Latest fixture comment" in output
    assert "Second fixture line" in output
    assert "Jane Smith" in output
    assert "2026-10-27 10:30 +0000" in output
    assert "Older comment" not in output
    assert "HIDDEN_AVATAR" not in output
    assert len(network[1]) == 1
    assert "comment" in network[1][0].url.params["fields"].split(",")
    result = invoke(["my", "today"], capsys)
    latest = result["issues"][0]["latest_comment"]
    assert latest["id"] == "2"
    assert latest["author"] == {"name": "Jane Smith"}
    assert latest["body"] == "Latest fixture comment\nSecond fixture line"
    assert result["issues"][1]["latest_comment"] is None
    assert "description" not in result["issues"][0]
    assert "comments" not in result["issues"][0]
    assert len(network[1]) == 2


def test_today_incomplete_comments_fetches_only_latest(
    network: Network,
    capsys: pytest.CaptureFixture[str],
) -> None:
    row = issue("WWRAP-10")
    row["fields"]["comment"] = {"total": 200, "comments": [{"body": "Oldest comment"}]}
    setup(network, [], [row, issue("WWRAP-11")])
    network[0]["issue/WWRAP-10/comment"] = {
        "total": 200,
        "startAt": 0,
        "comments": [
            {
                "id": "200",
                "body": "Most recent accessible comment",
                "created": "2026-10-28T10:30:00+00:00",
            }
        ],
    }
    result = invoke(["my", "today"], capsys)
    assert result["issues"][0]["latest_comment"]["id"] == "200"
    assert len(network[1]) == 2
    request = network[1][1]
    assert request.url.path.endswith("/issue/WWRAP-10/comment")
    assert request.url.params["maxResults"] == "1"
    assert request.url.params["orderBy"] == "-created"


@pytest.mark.parametrize("comments", [None, {"comments": [], "total": 0}])
def test_today_missing_comments_are_null(
    network: Network,
    capsys: pytest.CaptureFixture[str],
    comments: Any,
) -> None:
    row = issue("WWRAP-10")
    row["fields"]["comment"] = comments
    setup(network, [], [row])
    assert invoke(["my", "today"], capsys)["issues"][0]["latest_comment"] is None
    assert len(network[1]) == 1


def test_personal_search_pagination(network: Network, application: Path) -> None:
    pages: list[int] = []

    def search(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params["startAt"])
        pages.append(start)
        return httpx.Response(
            200,
            json={
                "startAt": start,
                "total": 2,
                "issues": [issue(f"WWRAP-{start + 1}")],
            },
        )

    network[0]["search"] = search
    client = JiraClient(load_config(application), SecretStr("fixture-pat-never-print"))
    try:
        result = personal.my_today(client, Config(), None, "priority")
    finally:
        client.close()
    assert len(result["issues"]) == 2
    assert pages == [0, 1]
