"""Shared project/assignee/category filtering for issue and Story discovery."""

import argparse
from collections.abc import Sequence
from typing import Any

from jira_cli.client import JiraClient
from jira_cli.config import UserError, project_key
from jira_cli.views import IssueViews


def flags(parser: argparse.ArgumentParser) -> None:
    ownership = parser.add_mutually_exclusive_group()
    ownership.add_argument(
        "--all",
        action="store_true",
        help="All visible assignees and categories in project.",
    )
    ownership.add_argument(
        "--mine", action="store_true", help="Only issues assigned to the current user."
    )
    parser.add_argument(
        "--include-done", action="store_true", help="Include all status categories."
    )


def category_clause(client: JiraClient, keys: Sequence[str]) -> str:
    metadata = client.get("statuscategory")
    if not isinstance(metadata, list) or any(not isinstance(v, dict) for v in metadata):
        raise UserError("Jira returned invalid status category metadata.")
    ids = []
    for key in dict.fromkeys(keys):
        matches = [v.get("id") for v in metadata if v.get("key") == key]
        if (
            len(matches) != 1
            or not str(matches[0]).isascii()
            or not str(matches[0]).isdecimal()
        ):
            raise UserError(
                "Configured status category is unavailable or ambiguous in Jira."
            )
        ids.append(str(matches[0]))
    return "statusCategory IN (" + ", ".join(ids) + ")"


def issues(
    args: argparse.Namespace, presenter: IssueViews, project: str
) -> list[dict[str, Any]]:
    client, config = presenter.client, presenter.config
    clauses = [f'project = "{project_key(project)}"']
    if args.command == "story":
        clauses.append('issuetype = "Story"')
    if args.mine or (not args.all and config.defaults.filters.assigned_to == "me"):
        clauses.append("assignee = currentUser()")
    if not (args.all or args.include_done):
        clauses.append(
            category_clause(client, config.defaults.filters.status_categories)
        )
        if args.action == "select":
            clauses.append("resolution IS EMPTY")
    return client.search(
        " AND ".join(clauses) + " ORDER BY updated DESC, key ASC",
        presenter.search_fields(),
    )
