import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from jira_cli.cli import main, parser
from jira_cli.config import UserError, load_config, set_config
from jira_cli.context import ContextStore, Selection
from jira_cli.views import display_name

pytestmark = pytest.mark.usefixtures("application")
type Network = tuple[dict[str, Any], list[httpx.Request]]
LINK = "customfield_23456"


def issue(key: str = "WWRAP-123", kind: str = "Story") -> dict[str, Any]:
    return {
        "key": key,
        "fields": {
            "summary": "Fixture business summary",
            "issuetype": {"name": kind, "iconUrl": "ICON"},
            "status": {
                "name": "In Progress",
                "id": "42",
                "statusCategory": {"key": "indeterminate"},
            },
            "priority": {"id": "7", "name": "Expedite", "iconUrl": "ICON"},
            "assignee": {
                "displayName": "Smith, Jane",
                "name": "unrelated-login",
                "emailAddress": "fixture.user@example.test",
                "avatarUrls": {"48x48": "AVATAR"},
            },
            LINK: "WWRAP-100",
            "description": "A fixture description",
        },
    }


def setup(network: Network) -> None:
    network[0]["issue/WWRAP-123"] = issue()
    network[0]["issue/WWRAP-100"] = issue("WWRAP-100", "Epic")
    network[0]["issue/WWRAP-100"]["fields"].pop(LINK)
    network[0]["field"] = [
        {"id": LINK, "schema": {"custom": "com.pyxis.greenhopper.jira:gh-epic-link"}}
    ]
    network[0]["search"] = lambda _: httpx.Response(
        200, json={"startAt": 0, "total": 1, "issues": [issue()]}
    )


def invoke(arguments: list[str], capsys: pytest.CaptureFixture[str]) -> Any:
    assert main([*arguments, "--format", "json"]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    return json.loads(output.out)


@pytest.mark.parametrize("command", ["issue", "story", "epic"])
def test_shared_normalized_views_context_and_type(
    network: Network,
    application: Path,
    capsys: pytest.CaptureFixture[str],
    command: str,
) -> None:
    setup(network)
    key = "WWRAP-100" if command == "epic" else "WWRAP-123"
    store = ContextStore(application, load_config(application))
    store.update(Selection(issue=key))
    before = (application / "state.json").read_bytes()
    result = invoke([command, "view"], capsys)
    assert result["key"] == key
    assert result["url"].endswith("/browse/" + key)
    assert result["status"] == "In Progress"
    assert result["priority"] == "Expedite"
    assert result["assignee"] == {
        "name": "Jane Smith",
        "email": "fixture.user@example.test",
    }
    assert result["type"] == ("Epic" if command == "epic" else "Story")
    assert result["epic"] == (None if command == "epic" else "WWRAP-100")
    assert "ICON" not in json.dumps(result)
    assert "AVATAR" not in json.dumps(result)
    if command == "epic":
        assert result["groups"][0]["stories"][0]["epic"] == key
        assert result["groups"][0]["stories"][0]["status"] == "In Progress"
    assert (application / "state.json").read_bytes() == before
    assert len([r for r in network[1] if r.url.path.endswith("/issue/" + key)]) == 1
    assert all(r.method == "GET" for r in network[1])


@pytest.mark.parametrize(
    ("command", "key"), [("story", "WWRAP-100"), ("epic", "WWRAP-123")]
)
def test_wrong_issue_type_fails_clearly(
    network: Network,
    capsys: pytest.CaptureFixture[str],
    command: str,
    key: str,
) -> None:
    setup(network)
    assert main([command, "view", key]) == 1
    assert f"not a {command.title()}" in capsys.readouterr().err
    assert invoke(["issue", "view", key], capsys)["key"] == key


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Smith, Jane", "Jane Smith"),
        ("O'Neill, Jane-Marie", "Jane-Marie O'Neill"),
        ("Jane Smith", "Jane Smith"),
        ("Smith, Jane, PhD", "Smith, Jane, PhD"),
        ("Smith, Jane (Contractor)", "Smith, Jane (Contractor)"),
        ("de la Cruz, Ana María", "de la Cruz, Ana María"),
        ("Smith, Jr.", "Smith, Jr."),
        ("Smith, Jr", "Smith, Jr"),
        ("Smith,", "Smith,"),
        (None, None),
        ("", None),
    ],
)
def test_conservative_name_conversion(value: Any, expected: str | None) -> None:
    assert display_name(value, first_last=True) == expected
    assert display_name(value, first_last=False) == (value or None)


@pytest.mark.parametrize(
    "assignee",
    [
        None,
        {},
        {"emailAddress": "not-a-name@example.test"},
        {"displayName": "Smith, Jane"},
        {"displayName": "Smith, Jane", "emailAddress": None},
    ],
)
def test_missing_assignee_and_email(
    network: Network,
    capsys: pytest.CaptureFixture[str],
    assignee: Any,
) -> None:
    setup(network)
    network[0]["issue/WWRAP-123"]["fields"]["assignee"] = assignee
    result = invoke(["story", "view", "WWRAP-123"], capsys)
    if assignee is None:
        assert result["assignee"] is None
    elif assignee.get("displayName"):
        assert result["assignee"] == {"name": "Jane Smith"}
    else:
        assert result["assignee"]["name"] is None


def test_priority_uses_verified_metadata_not_icon_filename(
    network: Network,
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup(network)
    row = network[0]["issue/WWRAP-123"]["fields"]
    row["priority"] = {"id": "7", "iconUrl": "https://invalid.test/low.svg"}
    network[0]["priority"] = [{"id": "7", "name": "Corporate Critical"}]
    assert (
        invoke(["issue", "view", "WWRAP-123"], capsys)["priority"]
        == "Corporate Critical"
    )
    row["priority"] = {"iconUrl": "https://invalid.test/high.svg"}
    assert invoke(["issue", "view", "WWRAP-123"], capsys)["priority"] is None
    row["priority"] = None
    assert invoke(["issue", "view", "WWRAP-123"], capsys)["priority"] is None


def test_details_are_whitelisted_and_normalized(
    network: Network, capsys: pytest.CaptureFixture[str]
) -> None:
    setup(network)
    row = network[0]["issue/WWRAP-123"]["fields"]
    row["attachment"] = [{"id": "1", "filename": "fixture.txt", "thumbnail": "AVATAR"}]
    row["comment"] = {
        "comments": [{"id": "2", "body": "Fixture", "author": row["assignee"]}]
    }
    row["issuelinks"] = [
        {"type": {"inward": "is blocked by"}, "inwardIssue": {"key": "OTHER-1"}}
    ]
    result = invoke(["story", "view", "WWRAP-123", "--detail"], capsys)
    assert result["description"] == "A fixture description"
    assert result["comments"][0]["author"]["name"] == "Jane Smith"
    assert result["attachments"][0]["filename"] == "fixture.txt"
    assert result["related_issues"] == [
        {"key": "OTHER-1", "relationship": "is blocked by"}
    ]
    assert "AVATAR" not in json.dumps(result)


@pytest.mark.parametrize("command", ["issue", "story"])
@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ([], (True, True)),
        (["--all"], (False, False)),
        (["--mine"], (True, True)),
        (["--include-done"], (True, False)),
        (["--mine", "--include-done"], (True, False)),
    ],
)
def test_default_list_filters_and_overrides(
    network: Network,
    capsys: pytest.CaptureFixture[str],
    command: str,
    options: list[str],
    expected: tuple[bool, bool],
) -> None:
    setup(network)
    assigned, categories = expected
    result = invoke([command, "list", *options, "--project", "OTHER"], capsys)
    jql = next(
        r.url.params["jql"] for r in network[1] if r.url.path.endswith("/search")
    )
    assert 'project = "OTHER"' in jql
    assert ("assignee = currentUser()" in jql) == assigned
    assert ("statusCategory IN (2, 4)" in jql) == categories
    assert ('issuetype = "Story"' in jql) == (command == "story")
    assert result[0]["status"] == "In Progress"
    assert result[0]["assignee"]["name"] == "Jane Smith"


def test_explicit_views_and_jql_ignore_list_filters(
    network: Network,
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup(network)
    network[0]["issue/WWRAP-123"]["fields"]["assignee"] = None
    network[0]["issue/WWRAP-123"]["fields"]["status"] = {"name": "Completed"}
    assert invoke(["story", "view", "WWRAP-123"], capsys)["status"] == "Completed"
    jql = "project = OTHER AND statusCategory = Done ORDER BY key DESC"
    invoke(["search", "--jql", jql], capsys)
    assert network[1][-1].url.params["jql"] == jql


def test_filter_and_display_configuration_backwards_compatible(
    network: Network,
    application: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup(network)
    before = (application / "config.yaml").read_bytes()
    config = load_config(application)
    assert config.defaults.filters.assigned_to == "me"
    assert config.output.assignee.include_email is True
    assert (application / "config.yaml").read_bytes() == before
    set_config(application, "defaults.filters.assigned_to", "all")
    set_config(application, "defaults.filters.status_categories", "[done]")
    set_config(application, "output.assignee.name_format", "original")
    set_config(application, "output.assignee.include_email", "false")
    result = invoke(["story", "list"], capsys)
    jql = next(
        r.url.params["jql"] for r in network[1] if r.url.path.endswith("/search")
    )
    assert "assignee" not in jql
    assert "statusCategory IN (3)" in jql
    assert result[0]["assignee"] == {"name": "Smith, Jane"}
    assert (application / ".env").read_text() == "JIRA_PAT=fixture-pat-never-print\n"


def test_story_selection_is_unresolved_and_context_only(
    network: Network,
    application: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup(network)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda: "1")
    assert main(["story", "select"]) == 0
    jql = next(
        r.url.params["jql"] for r in network[1] if r.url.path.endswith("/search")
    )
    assert "resolution IS EMPTY" in jql
    assert "currentUser()" in jql
    assert 'issuetype = "Story"' in jql
    assert (
        ContextStore(application, load_config(application)).selection().issue
        == "WWRAP-123"
    )
    assert all(r.method == "GET" for r in network[1])


def test_description_sources_exclude_each_other_before_requests(
    network: Network,
) -> None:
    with pytest.raises(SystemExit) as failure:
        main(
            [
                "create",
                "story",
                "--description",
                "inline",
                "--description-file",
                "file.txt",
            ]
        )
    assert failure.value.code == 2
    assert network[1] == []


def test_powershell_shaped_arguments_and_conflicting_ownership() -> None:
    args = parser().parse_args(
        [
            "--project",
            "WWRAP",
            "create",
            "epic",
            "--summary",
            'Fixture "quoted" title',
            "--description",
            "First\nSecond",
        ]
    )
    assert args.description == "First\nSecond"
    assert args.summary == 'Fixture "quoted" title'
    with pytest.raises(SystemExit):
        parser().parse_args(["story", "list", "--mine", "--all"])


def test_priority_metadata_is_cached_for_a_list_and_unknown_is_null(
    network: Network,
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup(network)
    rows = [issue(), issue("WWRAP-124")]
    for row in rows:
        row["fields"]["priority"] = {"id": "7", "iconUrl": "do-not-guess"}
    network[0]["priority"] = [{"id": "7", "name": "Local Custom Priority"}]
    network[0]["search"] = lambda _: httpx.Response(
        200, json={"startAt": 0, "total": 2, "issues": rows}
    )
    result = invoke(["story", "list"], capsys)
    assert [r["priority"] for r in result] == ["Local Custom Priority"] * 2
    assert len([r for r in network[1] if r.url.path.endswith("/priority")]) == 1
    network[0]["priority"] = []
    assert invoke(["story", "list"], capsys)[0]["priority"] is None


@pytest.mark.parametrize(
    "metadata",
    [{}, [None], [{"key": "new", "id": "invalid"}], [{"key": "new", "id": 2}] * 2],
)
def test_bad_category_metadata_fails_before_search(
    network: Network, metadata: Any
) -> None:
    setup(network)
    network[0]["statuscategory"] = metadata
    assert main(["story", "list"]) == 1
    assert not any(r.url.path.endswith("/search") for r in network[1])


def test_explicit_epic_parent_and_invalid_metadata(
    network: Network,
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup(network)
    row = network[0]["issue/WWRAP-123"]["fields"]
    row.pop(LINK)
    row["parent"] = {"key": "WWRAP-100", "fields": {"issuetype": {"name": "Epic"}}}
    assert invoke(["issue", "view", "WWRAP-123"], capsys)["epic"] == "WWRAP-100"
    assert not any(r.url.path.endswith("/field") for r in network[1])
    row.pop("parent")
    row[LINK] = "WWRAP-100"
    network[0]["field"] = {}
    assert main(["issue", "view", "WWRAP-123"]) == 1


def test_unknown_priority_metadata_must_not_guess(network: Network) -> None:
    setup(network)
    network[0]["issue/WWRAP-123"]["fields"]["priority"] = {"id": "7"}
    network[0]["priority"] = [{"id": "7", "name": "High"}, {"id": "7", "name": "Low"}]
    assert main(["issue", "view", "WWRAP-123"]) == 1


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("defaults.filters.status_categories", "[]"),
        ("defaults.filters.status_categories", "[Invalid]"),
        ("defaults.filters.assigned_to", "someone"),
        ("output.assignee.include_avatar", "true"),
        ("output.priority.source", "icon"),
    ],
)
def test_unsupported_configuration_fails_safely(
    application: Path, key: str, value: str
) -> None:
    with pytest.raises(UserError):
        set_config(application, key, value)
