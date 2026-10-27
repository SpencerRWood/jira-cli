"""Read-only personal workflows using Jira status categories and Epic Link metadata."""

import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from jira_cli.client import JiraClient
from jira_cli.config import Config, UserError, issue_key, project_key
from jira_cli.views import IssueViews

FIELDS = "summary,status,priority,assignee,issuetype,updated,duedate,issuelinks"
EPIC_SCHEMA = "com.pyxis.greenhopper.jira:gh-epic-link"


def fields(issue: dict[str, Any]) -> dict[str, Any]:
    value = issue.get("fields")
    if not isinstance(value, dict):
        raise UserError("Jira returned invalid personal workflow issue fields.")
    issue_key(str(issue.get("key", "")))
    return value


def done(issue: dict[str, Any]) -> bool:
    status = fields(issue).get("status")
    if not isinstance(status, dict) or not isinstance(
        status.get("statusCategory"), dict
    ):
        raise UserError(
            "Jira status category is unavailable; completion was not guessed."
        )
    category = status["statusCategory"].get("key")
    if not isinstance(category, str) or category not in {
        "new",
        "indeterminate",
        "done",
    }:
        raise UserError("Jira returned an unsupported status category.")
    return bool(category == "done")


def epic_link(client: JiraClient) -> tuple[str, str]:
    metadata = client.get("field")
    if not isinstance(metadata, list) or any(
        not isinstance(item, dict) for item in metadata
    ):
        raise UserError("Jira returned invalid field metadata.")
    matches = [
        item.get("id")
        for item in metadata
        if isinstance(item.get("schema"), dict)
        and item["schema"].get("custom") == EPIC_SCHEMA
    ]
    if len(matches) != 1 or not isinstance(matches[0], str):
        raise UserError("No unambiguous Data Center Epic Link field was advertised.")
    match = re.fullmatch(r"customfield_([0-9]+)", matches[0])
    if not match:
        raise UserError("Jira Epic Link field ID is unsupported.")
    return matches[0], "cf[" + match[1] + "]"


def personal_counts(client: JiraClient, link: str, jql_link: str) -> Counter[str]:
    stories = client.search(
        'issuetype = "Story" AND assignee = currentUser() '
        f"AND statusCategory != Done AND {jql_link} IS NOT EMPTY ORDER BY key ASC",
        FIELDS + "," + link,
    )
    counts: Counter[str] = Counter()
    seen: set[str] = set()
    for story in stories:
        data = fields(story)
        key = story["key"]
        if key in seen:
            continue
        seen.add(key)
        if not done(story) and data.get(link):
            counts[issue_key(str(data[link]))] += 1
    return counts


def epic_list(
    client: JiraClient,
    config: Config,
    project: str,
    mode: str,
    *,
    include_done: bool = False,
) -> list[dict[str, Any]]:
    link, jql_link = epic_link(client)
    counts = personal_counts(client, link, jql_link)
    user = client.object("myself")
    if not isinstance(user.get("name"), str) or not user["name"]:
        raise UserError("Jira current user identity is unavailable.")
    epics = client.search(
        f'project = "{project_key(project)}" AND issuetype = "Epic" ORDER BY key ASC',
        FIELDS,
    )
    result: dict[str, dict[str, Any]] = {}
    for epic in epics:
        data = fields(epic)
        completed = done(epic)
        assignee = data.get("assignee")
        mine = (
            isinstance(assignee, dict)
            and assignee.get("name") == user["name"]
            and (include_done or not completed)
        )
        count = counts[epic["key"]]
        include = {
            "mine": mine,
            "with-my-stories": count > 0,
            "all": True,
            "relevant": (
                (config.epics.include_assigned_open and mine)
                or (
                    config.epics.include_with_my_active_stories
                    and count > 0
                    and (
                        not completed
                        or config.epics.include_completed_epics_with_active_stories
                    )
                )
            ),
        }[mode]
        if include:
            result[epic["key"]] = {
                **epic,
                "url": client.url(epic["key"]),
                "my_active_story_count": count,
                "completed_with_active_personal_stories": completed and count > 0,
            }
    return list(result.values())


def epic_view(
    client: JiraClient,
    key: str,
    config: Config,
    *,
    all_stories: bool,
    detail: bool = False,
) -> dict[str, Any]:
    presenter = IssueViews(client, config)
    epic = presenter.get(key, "Epic")
    completed = done(epic)
    _, jql_link = epic_link(client)
    personal = client.search(
        f'{jql_link} = "{issue_key(key)}" AND issuetype = "Story" '
        "AND assignee = currentUser() ORDER BY status ASC, priority DESC, key ASC",
        FIELDS,
    )
    active = len({s["key"] for s in personal if not done(s)})
    stories = (
        client.search(
            f'{jql_link} = "{issue_key(key)}" AND issuetype = "Story" '
            "ORDER BY status ASC, priority DESC, key ASC",
            FIELDS,
        )
        if all_stories
        else personal
    )
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    seen: set[str] = set()
    for story in stories:
        story_fields = fields(story)
        if story["key"] in seen:
            continue
        seen.add(story["key"])
        status = story_fields.get("status") or {}
        priority = story_fields.get("priority") or {}
        if not isinstance(status, dict) or not isinstance(priority, dict):
            raise UserError("Jira returned invalid Story grouping fields.")
        normalized = presenter.normalize(story)
        group = (
            str(normalized["status"] or "Unknown"),
            str(normalized["priority"] or "None"),
        )
        normalized["epic"] = key
        groups.setdefault(group, []).append(normalized)
    return {
        **presenter.normalize(epic, detail=detail, include_description=True),
        "story_scope": "all" if all_stories else "mine",
        "my_active_story_count": active,
        "completed_with_active_personal_stories": completed and active > 0,
        "groups": [
            {"status": status, "priority": priority, "stories": rows}
            for (status, priority), rows in groups.items()
        ],
    }


def blocking_links(data: dict[str, Any]) -> tuple[list[str], list[str]]:
    blockers = []
    inferred = []
    links = data.get("issuelinks") or []
    if not isinstance(links, list):
        raise UserError("Jira returned invalid issue links.")
    for link in links:
        if not isinstance(link, dict) or not isinstance(link.get("type"), dict):
            raise UserError("Jira returned invalid issue link metadata.")
        dependency = link.get("inwardIssue")
        if str(
            link["type"].get("inward", "")
        ).casefold() == "is blocked by" and isinstance(dependency, dict):
            try:
                if not done(dependency):
                    blockers.append(issue_key(str(dependency.get("key", ""))))
            except UserError:
                inferred.append(
                    "Blocking link present; linked issue status is unavailable."
                )
    return sorted(set(blockers)), inferred


def risks(issue: dict[str, Any], now: datetime, stale_days: int) -> dict[str, Any]:
    data = fields(issue)
    blockers, inferred = blocking_links(data)
    if due := data.get("duedate"):
        try:
            if (
                datetime.strptime(str(due), "%Y-%m-%d").replace(tzinfo=UTC).date()
                < now.date()
            ):
                inferred.append("Overdue by due date; not proof of a blocker.")
        except ValueError as exc:
            raise UserError("Jira returned an invalid due date.") from exc
    if updated := data.get("updated"):
        try:
            timestamp = datetime.fromisoformat(str(updated))
            if timestamp.tzinfo is None:
                raise ValueError("Missing time zone")
            if (now - timestamp).days >= stale_days:
                inferred.append("Stale by last-updated time; not proof of a blocker.")
        except ValueError as exc:
            raise UserError("Jira returned an invalid updated timestamp.") from exc
    return {"blockers": blockers, "inferred_risks": inferred}


def my_today(
    client: JiraClient, config: Config, project: str | None, sort: str
) -> dict[str, Any]:
    order = {
        "due-date": "duedate ASC",
        "priority": "priority DESC",
        "status": "status ASC",
        "staleness": "updated ASC",
    }[sort]
    restriction = f' AND project = "{project_key(project)}"' if project else ""
    rows = client.search(
        "assignee = currentUser() AND resolution IS EMPTY"
        + restriction
        + f" ORDER BY {order}, key ASC",
        FIELDS,
    )
    now = datetime.now(UTC)
    return {
        "scope": project or "all-accessible-projects",
        "sort": sort,
        "observed_at": now.isoformat(),
        "time_zone": "UTC",
        "issues": [
            {
                **issue,
                "url": client.url(str(issue.get("key", ""))),
                **risks(issue, now, config.reporting.aging_threshold_days),
            }
            for issue in rows
        ],
    }
