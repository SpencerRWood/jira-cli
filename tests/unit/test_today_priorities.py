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
                "name": "To Do",
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
    capsys: pytest.CaptureFixture[str],
    args: list[str],
    count: int,
) -> None:
    review = story(9)
    review["fields"]["status"]["name"] = "Review"
    rows = [
        review,
        *[story(i) for i in range(1, 9)],
        review,
        story(10, category="done"),
        story(11, resolution={"id": "1"}),
    ]

    def search(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params["startAt"])
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
    assert result["review_total"] == 1
    assert result["review"][0]["needs_review"] is True
    assert result["review"][0]["review_since"] is None
    assert all(row["needs_review"] is False for row in result["actionable"])
    assert "blocked" not in result


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


def test_config_and_no_review(
    network: Network, application: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    set_config(application, "my_today.default_limit", "1")
    network[0]["search"] = {"startAt": 0, "total": 2, "issues": [story(1), story(2)]}
    assert main(["my", "today", "--no-review"]) == 0
    output = capsys.readouterr().out
    assert "Showing 1 of 2 actionable stories" in output
    assert "No stories needing review" not in output


@pytest.mark.parametrize("status", ["Peer Review", "17"])
def test_configured_review_status_name_or_id(
    network: Network, application: Path, capsys: pytest.CaptureFixture[str], status: str
) -> None:
    set_config(application, "my_today.review.statuses", f"[{status!r}]")
    row = story(1)
    row["fields"]["status"]["name"] = "Peer Review"
    network[0]["search"] = {"startAt": 0, "total": 1, "issues": [row]}
    assert main(["my", "today", "--format", "json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["actionable"] == []
    assert result["review_total"] == 1
    assert result["review"][0]["key"] == "WWRAP-1"


def test_review_table_and_suppression(
    network: Network, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = [story(i) for i in range(1, 9)]
    for row in rows:
        row["fields"]["status"]["name"] = "Review"
    network[0]["search"] = {"startAt": 0, "total": 8, "issues": rows}
    assert main(["my", "today", "--top", "1"]) == 0
    output = capsys.readouterr().out
    assert "STORIES NEEDING REVIEW" in output
    assert "Review Since" in output
    assert "WWRAP-8" in output
    assert "Showing 0 of 0 actionable stories" in output
    assert main(["my", "today", "--no-review", "--format", "json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["show_review"] is False
    assert len(result["review"]) == 8


def test_flags_and_dependencies_do_not_imply_review(
    network: Network, capsys: pytest.CaptureFixture[str]
) -> None:
    row = story(
        1,
        customfield_19=[{"value": "Impediment"}],
        issuelinks=[{"type": {"inward": "is blocked by"}, "inwardIssue": story(2)}],
    )
    network[0]["field"] = [{"id": "customfield_19", "name": "Flagged"}]
    network[0]["search"] = {"startAt": 0, "total": 1, "issues": [row]}
    assert main(["my", "today", "--format", "json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["review"] == []
    assert result["actionable"][0]["blockers"] == ["WWRAP-2"]


@pytest.mark.parametrize(
    "args", [["--top", "0"], ["--top", "-1"], ["--top", "x"], ["--top", "3", "--all"]]
)
def test_bad_limit(args: list[str]) -> None:
    with pytest.raises(SystemExit):
        parser().parse_args(["my", "today", *args])
