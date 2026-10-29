import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from jira_cli.cli import main, parser
from jira_cli.config import set_config

pytestmark = pytest.mark.usefixtures("application")
type Network = tuple[dict[str, Any], list[httpx.Request]]


def story(
    number: int, *, priority: str = "custom-high", category: str = "new", **extra: Any
) -> dict[str, Any]:
    return {
        "key": f"WWRAP-{number}",
        "fields": {
            "issuetype": {"name": "Story"},
            "summary": f"Story {number}",
            "status": {
                "id": "17",
                "name": "Review",
                "statusCategory": {"key": category},
            },
            "priority": {"id": priority, "name": priority},
            **extra,
        },
    }


@pytest.mark.parametrize(
    ("args", "count"),
    [([], 5), (["--top", "3"], 3), (["--all"], 8), (["--top", "10"], 8)],
)
def test_pagination_classification_and_limits(
    network: Network,
    application: Path,
    capsys: pytest.CaptureFixture[str],
    args: list[str],
    count: int,
) -> None:
    set_config(application, "my_today.blocked.statuses", "[Waiting]")
    network[0]["field"] = [{"id": "customfield_19", "name": "Flagged"}]
    blocked = story(9, customfield_19=[{"value": "Impediment"}])
    blocked["fields"]["status"]["name"] = "Waiting"
    rows = [
        blocked,
        *[story(i) for i in range(1, 9)],
        blocked,
        story(10, category="done"),
        story(11, resolution={"id": "1"}),
    ]

    def search(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params["startAt"])
        assert "customfield_19" in request.url.params["fields"]
        return httpx.Response(
            200,
            json={
                "startAt": start,
                "total": len(rows),
                "issues": rows[start : start + 2],
            },
        )

    network[0]["search"] = search
    assert main(["my", "today", "--format", "json", *args]) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(result["actionable"]) == count
    assert result["actionable_total"] == 8
    assert result["issues"] == result["actionable"]
    assert result["blocked_total"] == 1
    assert result["blocked"][0]["blocking_indicators"] == [
        "configured-status",
        "issue-flag",
    ]
    assert result["blocked"][0]["blocked_since"] is None


def test_urgency_uses_server_priority_order(
    network: Network, capsys: pytest.CaptureFixture[str]
) -> None:
    # Server sequence is high to low; identifiers and labels cannot imply rank.
    rows = [
        story(1, duedate="2999-01-01"),
        story(2, category="indeterminate", duedate="2999-02-01"),
        story(3, updated="2026-01-02T00:00:00+00:00"),
        story(4, updated="2026-01-03T00:00:00+00:00"),
        story(5, priority="aaa-low", duedate="2000-01-01"),
        story(6, priority="aaa-low", duedate="2998-01-01"),
    ]
    network[0]["search"] = {"startAt": 0, "total": len(rows), "issues": rows}
    assert main(["my", "today", "--all", "--format", "json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert [r["key"] for r in result["actionable"]] == [
        "WWRAP-5",
        "WWRAP-2",
        "WWRAP-1",
        "WWRAP-4",
        "WWRAP-3",
        "WWRAP-6",
    ]


def test_config_and_no_blocked(
    network: Network, application: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    set_config(application, "my_today.default_limit", "1")
    network[0]["search"] = {"startAt": 0, "total": 2, "issues": [story(1), story(2)]}
    assert main(["my", "today", "--no-blocked"]) == 0
    output = capsys.readouterr().out
    assert "Showing 1 of 2 actionable stories" in output
    assert "No blocked stories" not in output


@pytest.mark.parametrize(
    "args", [["--top", "0"], ["--top", "-1"], ["--top", "x"], ["--top", "3", "--all"]]
)
def test_bad_limit(args: list[str]) -> None:
    with pytest.raises(SystemExit):
        parser().parse_args(["my", "today", *args])
