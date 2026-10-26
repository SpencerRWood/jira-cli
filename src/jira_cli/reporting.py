"""Deterministic UTC report snapshots with verified status-transition evidence."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from jira_cli import personal
from jira_cli.client import JiraClient
from jira_cli.config import Config, UserError, issue_key, project_key

REPORT_FIELDS = personal.FIELDS + ",created"


@dataclass(frozen=True)
class Request:
    kind: str
    project: str
    mine: bool = False
    since: str | None = None


def timestamp(value: object) -> datetime:
    try:
        if not isinstance(value, str):
            raise ValueError("Missing timestamp")
        result = datetime.fromisoformat(value)
        if result.tzinfo is None:
            raise ValueError("Missing time zone")
        return result.astimezone(UTC)
    except ValueError as exc:
        raise UserError(
            "Report requires valid Jira timestamps with time zones."
        ) from exc


def since_date(value: str) -> datetime:
    try:
        result = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
        if result.strftime("%Y-%m-%d") != value:
            raise ValueError("Not canonical ISO date")
        return result
    except ValueError as exc:
        raise UserError("Use --since YYYY-MM-DD (midnight UTC).") from exc


def status_categories(client: JiraClient) -> dict[str, str]:
    values = client.get("status")
    if not isinstance(values, list):
        raise UserError("Jira report status metadata is unavailable.")
    result: dict[str, str] = {}
    for value in values:
        if not isinstance(value, dict) or not isinstance(value.get("id"), str):
            raise UserError("Jira report status metadata is invalid.")
        category = value.get("statusCategory")
        if not isinstance(category, dict) or category.get("key") not in (
            "new",
            "indeterminate",
            "done",
        ):
            raise UserError("Jira report status category is unsupported.")
        if value["id"] in result and result[value["id"]] != category["key"]:
            raise UserError("Jira report status IDs have conflicting categories.")
        result[value["id"]] = category["key"]
    return result


def status_changes(detail: dict[str, Any]) -> list[tuple[datetime, str, str]]:
    log = detail.get("changelog")
    if not isinstance(log, dict):
        raise UserError("Weekly report requires complete accessible issue history.")
    rows = log.get("histories")
    if (
        not isinstance(rows, list)
        or type(log.get("total")) is not int
        or log.get("startAt") != 0
        or log["total"] != len(rows)
    ):
        raise UserError(
            "Jira returned incomplete history; no weekly report was produced."
        )
    events = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            raise UserError("Jira history entries require unique IDs.")
        if row["id"] in seen:
            raise UserError("Jira history contains duplicate entries.")
        seen.add(row["id"])
        created = timestamp(row.get("created"))
        items = row.get("items")
        if not isinstance(items, list):
            raise UserError("Jira history items are invalid.")
        for item in items:
            if not isinstance(item, dict):
                raise UserError("Jira history item is invalid.")
            if item.get("fieldId", item.get("field")) == "status":
                before, after = item.get("from"), item.get("to")
                if not isinstance(before, str) or not isinstance(after, str):
                    raise UserError("Jira status history is missing status IDs.")
                events.append((created, before, after))
    return sorted(events, key=lambda event: event[0])


def weekly_flags(
    client: JiraClient,
    issue: dict[str, Any],
    categories: dict[str, str],
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    data = personal.fields(issue)
    created, updated = timestamp(data.get("created")), timestamp(data.get("updated"))
    active = not personal.done(issue)
    if updated < start:
        return {"completed_at": [], "carried_over": active and created < start}
    detail = client.object(
        "issue/" + issue_key(issue["key"]),
        {"fields": "status,created,updated", "expand": "changelog"},
    )
    detail_data = personal.fields(detail)
    if detail.get("key") != issue["key"] or detail_data != {
        name: data.get(name) for name in ["status", "created", "updated"]
    }:
        raise UserError(
            "Issue changed during report collection; retry for a consistent snapshot."
        )
    events = status_changes(detail)
    status = data["status"].get("id")
    if (
        not isinstance(status, str)
        or categories.get(status) != data["status"]["statusCategory"]["key"]
    ):
        raise UserError("Current issue status does not match report status metadata.")
    completed = []
    for when, before, after in reversed(events):
        if when < created or when > updated or when >= end:
            raise UserError("Jira status history is outside the report snapshot.")
        if before not in categories or after not in categories or status != after:
            raise UserError(
                "Jira status history cannot be reconciled; no completion was guessed."
            )
        if (
            start <= when
            and categories[before] != "done"
            and categories[after] == "done"
        ):
            completed.append(when.isoformat())
        if when >= start:
            status = before
        else:
            # The first older transition confirms the window-start state.
            break
    return {
        "completed_at": sorted(completed),
        "carried_over": active and created < start and categories[status] != "done",
    }


def name(data: dict[str, Any], field: str) -> str:
    value = data.get(field)
    if value is None:
        return ""
    if not isinstance(value, dict) or not isinstance(value.get("name"), str):
        raise UserError("Jira report display fields are invalid.")
    return str(value["name"])


def report_row(
    client: JiraClient, issue: dict[str, Any], config: Config, end: datetime
) -> dict[str, Any]:
    data = personal.fields(issue)
    created, updated = timestamp(data.get("created")), timestamp(data.get("updated"))
    if created > updated or updated >= end:
        raise UserError(
            "Issue changed during report collection; retry for a consistent snapshot."
        )
    if not isinstance(data.get("summary"), str):
        raise UserError("Jira report issue summary is invalid.")
    risks = personal.risks(issue, end, config.reporting.aging_threshold_days)
    due = data.get("duedate")
    active = not personal.done(issue)
    return {
        "key": issue["key"],
        "summary": data["summary"],
        "status": name(data, "status"),
        "priority": name(data, "priority"),
        "assignee": name(data, "assignee"),
        "created": created.isoformat(),
        "updated": updated.isoformat(),
        "due_date": due or "",
        "url": client.url(issue["key"]) if config.output.show_issue_urls else "",
        "currently_active": active,
        "overdue": bool(active and due and since_date(str(due)).date() < end.date()),
        "stale": end - updated >= timedelta(days=config.reporting.aging_threshold_days),
        **risks,
        "completed_at": [],
        "carried_over": False,
    }


def build(
    client: JiraClient,
    config: Config,
    request: Request,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    end = now or datetime.now(UTC)
    if end.tzinfo is None:
        raise UserError("Report observation time requires a time zone.")
    end = end.astimezone(UTC)
    kind, project, mine, since = (
        request.kind,
        request.project,
        request.mine,
        request.since,
    )
    start = (
        since_date(since)
        if since is not None
        else (
            end - timedelta(days=config.reporting.weekly_lookback_days)
            if kind == "weekly"
            else end.replace(hour=0, minute=0, second=0, microsecond=0)
        )
    )
    if start > end or (since and start == end):
        raise UserError("Report start must be earlier than its observation time.")
    restriction = " AND assignee = currentUser()" if mine else ""
    # Filter timestamps locally: Jira interprets JQL date literals in the user's
    # Jira time zone, which need not be UTC. This also retains older carried work.
    issues = client.search(
        f'project = "{project_key(project)}"' + restriction + " ORDER BY key ASC",
        REPORT_FIELDS,
    )
    categories = status_categories(client) if kind == "weekly" else {}
    result = {}
    for issue in issues:
        row = report_row(client, issue, config, end)
        if kind == "weekly":
            row.update(weekly_flags(client, issue, categories, start, end))
            include = row["currently_active"] or bool(row["completed_at"])
        else:
            include = (
                row["currently_active"]
                and (kind != "aging" or row["stale"])
                and (not since or timestamp(row["updated"]) >= start)
            )
        if include:
            result[row["key"]] = row
    rows = [result[key] for key in sorted(result)]
    return {
        "report": kind,
        "project": project,
        "mine": mine,
        "time_zone": "UTC",
        "window_start": start.isoformat(),
        "window_end_exclusive": end.isoformat(),
        "aging_threshold_days": config.reporting.aging_threshold_days,
        "definitions": {
            "active": "Current status category is not Done; ignores resolution.",
            "completed": "Non-Done to Done status-category transition in [start, end).",
            "carried_over": "Active now and at start, created before start.",
            "since": "Weekly window; daily/aging explicit --since filters last update.",
            "history": "Current status categories; incomplete/unknown history fails.",
            "scope": "Current project/assignee visibility, not historical ownership.",
            "stale": "Last update age >= threshold days, evaluated at window end.",
            "overdue": "Due date before observation UTC date; active rows only.",
            "blockers": "Active inward 'is blocked by' links; other risks inferred.",
        },
        "counts": {
            "issues": len(rows),
            "currently_active": sum(r["currently_active"] for r in rows),
            "completed_during_window": sum(bool(r["completed_at"]) for r in rows),
            "carried_over": sum(r["carried_over"] for r in rows),
            "overdue": sum(r["currently_active"] and r["overdue"] for r in rows),
            "blocked": sum(r["currently_active"] and bool(r["blockers"]) for r in rows),
        },
        "issues": rows,
    }
