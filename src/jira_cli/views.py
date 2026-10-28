"""One issue retrieval and business presentation path for Issue, Story and Epic."""

from datetime import datetime
from typing import Any

from jira_cli.client import JiraClient
from jira_cli.config import Config, UserError, issue_key

EPIC_SCHEMA = "com.pyxis.greenhopper.jira:gh-epic-link"
EPIC_NAME_SCHEMA = "com.pyxis.greenhopper.jira:gh-epic-label"
FIELDS = "summary,status,priority,assignee,issuetype,parent,updated,duedate,issuelinks"


def display_name(value: object, *, first_last: bool) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    parts = [part.strip() for part in value.split(",")]
    # Be conservative: multipart names, suffixes and annotations remain verbatim.
    if (
        first_last
        and len(parts) == 2
        and all(
            part
            and all(char.isalpha() or char in "-'\u2019" for char in part)
            and part.casefold() not in {"jr", "sr", "ii", "iii", "iv", "phd", "md"}
            for part in parts
        )
    ):
        return parts[1] + " " + parts[0]
    return value


def named(value: object) -> str | None:
    if isinstance(value, dict) and isinstance(value.get("name"), str):
        return str(value["name"]) or None
    return None


class IssueViews:
    def __init__(self, client: JiraClient, config: Config) -> None:
        self.client = client
        self.config = config
        self._fields: list[dict[str, Any]] | None = None
        self._priorities: dict[str, str] | None = None
        self._epic_names: dict[str, str | None] = {}

    def get(self, key: str, expected: str | None = None) -> dict[str, Any]:
        issue = self.client.issue(key)
        data = self.fields(issue)
        kind = named(data.get("issuetype"))
        if expected and (kind is None or kind.casefold() != expected.casefold()):
            raise UserError(
                f"Selected issue is not a {expected}; use jira issue view for any type."
            )
        return issue

    @staticmethod
    def fields(issue: dict[str, Any]) -> dict[str, Any]:
        value = issue.get("fields")
        if not isinstance(value, dict):
            raise UserError("Jira returned invalid issue fields.")
        issue_key(str(issue.get("key", "")))
        return value

    def field_metadata(self) -> list[dict[str, Any]]:
        if self._fields is None:
            values = self.client.field_metadata
            if values is None:
                values = self.client.get("field")
            if not isinstance(values, list) or any(
                not isinstance(v, dict) for v in values
            ):
                raise UserError("Jira returned invalid field metadata.")
            self._fields = values
            self.client.field_metadata = values
        return self._fields

    def epic_fields(self) -> list[str]:
        matches = [
            str(f["id"])
            for f in self.field_metadata()
            if isinstance(f.get("id"), str)
            and isinstance(f.get("schema"), dict)
            and f["schema"].get("custom") == EPIC_SCHEMA
        ]
        if len(matches) > 1:
            raise UserError("Jira Epic Link field metadata is ambiguous.")
        return matches

    def search_fields(self) -> str:
        return ",".join([FIELDS, *self.epic_fields(), *self.epic_name_fields()])

    def epic_name_fields(self) -> list[str]:
        matches = [
            str(f["id"])
            for f in self.field_metadata()
            if isinstance(f.get("id"), str)
            and isinstance(f.get("schema"), dict)
            and f["schema"].get("custom") == EPIC_NAME_SCHEMA
        ]
        if len(matches) > 1:
            raise UserError("Jira Epic Name field metadata is ambiguous.")
        return matches

    def epic_name(self, data: dict[str, Any]) -> str | None:
        for key in self.epic_name_fields():
            if isinstance(value := data.get(key), str) and value.strip():
                return value
        return None

    def enrich(self, rows: list[dict[str, Any]]) -> None:
        """Resolve distinct parent Epics in bounded batches, only for this command."""
        if not self.epic_name_fields():
            return
        for row in rows:
            data = self.fields(row)
            if named(data.get("issuetype")) == "Epic":
                self._epic_names[row["key"]] = self.epic_name(data)
        keys = sorted({key for row in rows if (key := self.epic(self.fields(row)))})
        missing = [key for key in keys if key not in self._epic_names]
        for start in range(0, len(missing), 100):
            batch = missing[start : start + 100]
            self._epic_names.update(dict.fromkeys(batch))
            epics = self.client.search(
                "key IN (" + ",".join(batch) + ') AND issuetype = "Epic"',
                ",".join(["issuetype", *self.epic_name_fields()]),
            )
            for epic in epics:
                if epic.get("key") in batch:
                    self._epic_names[epic["key"]] = self.epic_name(self.fields(epic))

    def normalize_many(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self.enrich(rows)
        return [self.normalize(row) for row in rows]

    def priority(self, value: object) -> str | None:
        if name := named(value):
            return name
        if not isinstance(value, dict) or not isinstance(value.get("id"), str):
            return None
        if self._priorities is None:
            values = self.client.get("priority")
            if not isinstance(values, list) or any(
                not isinstance(v, dict) for v in values
            ):
                raise UserError("Jira returned invalid priority metadata.")
            self._priorities = {}
            for item in values:
                if isinstance(item.get("id"), str) and (name := named(item)):
                    if (
                        item["id"] in self._priorities
                        and self._priorities[item["id"]] != name
                    ):
                        raise UserError("Jira priority IDs are ambiguous.")
                    self._priorities[item["id"]] = name
        return self._priorities.get(value["id"])

    def assignee(self, value: object) -> dict[str, str | None] | None:
        if not isinstance(value, dict):
            return None
        options = self.config.output.assignee
        result = {
            "name": display_name(
                value.get("displayName"), first_last=options.name_format == "first_last"
            )
        }
        if (
            options.include_email
            and isinstance(value.get("emailAddress"), str)
            and value["emailAddress"]
        ):
            result["email"] = value["emailAddress"]
        return result

    def epic(self, data: dict[str, Any]) -> str | None:
        parent = data.get("parent")
        if (
            isinstance(parent, dict)
            and isinstance(parent.get("fields"), dict)
            and named(parent["fields"].get("issuetype")) == "Epic"
        ):
            return issue_key(str(parent.get("key", "")))
        if not any(
            key.startswith("customfield_") and value for key, value in data.items()
        ):
            return None
        matches = [data.get(key) for key in self.epic_fields() if data.get(key)]
        return issue_key(str(matches[0])) if matches else None

    def normalize(
        self,
        issue: dict[str, Any],
        *,
        detail: bool = False,
        include_description: bool = False,
    ) -> dict[str, Any]:
        data = self.fields(issue)
        epic = self.epic(data)
        result = {
            "key": issue["key"],
            "url": self.client.url(issue["key"]),
            "summary": data.get("summary"),
            "type": named(data.get("issuetype")),
            "priority": self.priority(data.get("priority")),
            "status": named(data.get("status")),
            "assignee": self.assignee(data.get("assignee")),
            "epic": epic,
            "epic_key": epic,
            "epic_name": self.epic_name(data)
            if named(data.get("issuetype")) == "Epic"
            else self._epic_names.get(epic or ""),
        }
        if include_description or detail:
            result["description"] = data.get("description")
            comments = self.comments(issue, detail=detail)
            result["latest_comment"] = comments[0] if comments else None
        if detail:
            result.update(self.details(data))
            result["comments"] = comments
            result["comment_limit"] = self.config.output.comment_limit
        return result

    def comments(self, issue: dict[str, Any], *, detail: bool) -> list[dict[str, Any]]:
        embedded = self.fields(issue).get("comment")
        if embedded is None:
            return []
        if not isinstance(embedded, dict):
            raise UserError("Jira returned invalid comments.")
        rows = embedded.get("comments") or []
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise UserError("Jira returned invalid comments.")
        total = embedded.get("total", len(rows))
        if type(total) is not int or total < 0:
            raise UserError("Jira returned an invalid comment total.")
        limit = self.config.output.comment_limit if detail else 1
        if total > len(rows):
            rows = self.client.comments(issue["key"], limit)
        rows = sorted(rows, key=comment_time, reverse=True)[:limit]
        return [
            {
                **{key: row.get(key) for key in ["id", "body", "created", "updated"]},
                "author": self.assignee(row.get("author")),
            }
            for row in rows
        ]

    def details(self, data: dict[str, Any]) -> dict[str, Any]:
        attachments = data.get("attachment") or []
        links = data.get("issuelinks") or []
        if not all(isinstance(v, list) for v in [attachments, links]):
            raise UserError("Jira returned invalid issue detail lists.")
        return {
            "dates": {
                key: data.get(key)
                for key in ["created", "updated", "duedate", "resolutiondate"]
            },
            "attachments": [
                {
                    key: row.get(key)
                    for key in ["id", "filename", "size", "mimeType", "content"]
                }
                for row in attachments
                if isinstance(row, dict)
            ],
            "related_issues": [
                self.related(row) for row in links if isinstance(row, dict)
            ],
        }

    @staticmethod
    def related(row: dict[str, Any]) -> dict[str, Any]:
        inward = "inwardIssue" in row
        issue = row.get("inwardIssue" if inward else "outwardIssue")
        kind = row.get("type")
        return {
            "key": issue.get("key") if isinstance(issue, dict) else None,
            "relationship": kind.get("inward" if inward else "outward")
            if isinstance(kind, dict)
            else None,
        }


def comment_time(row: dict[str, Any]) -> float:
    try:
        value = datetime.fromisoformat(str(row.get("created", "")))
        return value.timestamp() if value.tzinfo else float("-inf")
    except ValueError:
        return float("-inf")
