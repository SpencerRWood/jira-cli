"""Metadata-aware issue mutations; context changes only after confirmed creation."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jira_cli.client import JiraClient, WriteUncertainError
from jira_cli.config import Config, UserError, issue_key, project_key
from jira_cli.context import ContextStore, Selection
from jira_cli.metadata import (
    Ask,
    Fields,
    choices,
    epic_field,
    field_map,
    find_field,
    prepare,
)


@dataclass
class Creation:
    kind: str
    project: str
    values: dict[str, Any]
    epic: str | None = None
    update_context: bool = True
    watchers: tuple[str, ...] = ()


class Mutations:
    def __init__(
        self, client: JiraClient, store: ContextStore, config: Config, ask: Ask
    ) -> None:
        self.client = client
        self.store = store
        self.config = config
        self.ask = ask

    def gate(
        self, permission: str, *, project: str | None = None, issue: str | None = None
    ) -> None:
        server = self.client.object("serverInfo")
        version = str(server.get("version", ""))
        match = re.match(r"^(\d+)\.(\d+)", version)
        if (
            server.get("deploymentType") not in ("Server", "Data Center")
            or not match
            or tuple(map(int, match.groups())) < (8, 14)
        ):
            raise UserError(
                "Writes require a verified PAT-capable Jira Server/Data Center "
                "version (8.14+)."
            )
        self.permission(permission, project=project, issue=issue)

    def permission(
        self, permission: str, *, project: str | None = None, issue: str | None = None
    ) -> None:
        target = (
            {"issueKey": issue_key(issue)}
            if issue
            else {"projectKey": project_key(project or "")}
        )
        data = self.client.object(
            "mypermissions", {**target, "permissions": permission}
        )
        permissions = data.get("permissions")
        entry = permissions.get(permission) if isinstance(permissions, dict) else None
        if not isinstance(entry, dict) or entry.get("havePermission") is not True:
            raise UserError(
                "Jira did not grant the required " + permission + " permission."
            )

    def edit_metadata(self, issue: str) -> Fields:
        data = self.client.object("issue/" + issue_key(issue) + "/editmeta")
        fields = data.get("fields")
        if not isinstance(fields, dict) or any(
            not isinstance(v, dict) for v in fields.values()
        ):
            raise UserError("Jira edit metadata is not a field mapping.")
        return fields

    def check_epic(self, key: str) -> None:
        data = self.client.issue(issue_key(key))
        fields = data.get("fields")
        kind = fields.get("issuetype") if isinstance(fields, dict) else None
        if not isinstance(kind, dict) or kind.get("name") != "Epic":
            raise UserError("The selected issue is not an accessible Epic.")

    def check_targets(
        self,
        fields: dict[str, Any],
        specs: Fields,
        *,
        project: str | None = None,
        issue: str | None = None,
    ) -> None:
        for key, value in fields.items():
            if (
                value is not None
                and specs[key].get("schema", {}).get("custom")
                == "com.pyxis.greenhopper.jira:gh-epic-link"
            ):
                self.check_epic(value)
        if "assignee" in fields:
            self.permission("ASSIGN_ISSUES", project=project, issue=issue)
        if fields.get("assignee") is not None:
            self.check_assignable(
                fields["assignee"]["name"], project=project, issue=issue
            )

    def check_assignable(
        self, name: str, *, project: str | None = None, issue: str | None = None
    ) -> None:
        target = (
            {"issueKey": issue_key(issue)}
            if issue
            else {"project": project_key(project or "")}
        )
        users = self.client.get("user/assignable/search", {**target, "username": name})
        if not isinstance(users, list) or not any(
            isinstance(user, dict)
            and user.get("name") == name
            and user.get("active") is True
            for user in users
        ):
            raise UserError("The selected assignee is not an active assignable user.")

    def create(self, request: Creation) -> dict[str, Any]:
        project = project_key(request.project)
        self.gate("CREATE_ISSUES", project=project)
        if request.watchers:
            self.check_watchers(request.watchers, project=project)
        metadata = self.client.fields(project, request.kind)
        specs = {
            key: spec
            for key, spec in field_map(metadata["values"]).items()
            if key not in {"project", "issuetype", "id", "key"}
        }
        values = dict(request.values)
        if request.kind == "Story" and not any(
            find_field(name, specs) == "assignee" for name in values
        ):
            values["assignee"] = self.current_username()
        if request.epic:
            key = epic_field(specs)
            if key in values:
                raise UserError("Epic Link was supplied twice.")
            values[key] = issue_key(request.epic)
        fields = prepare(values, specs, self.ask, required=True)
        self.check_targets(fields, specs, project=project)
        fields.update(
            project={"key": project}, issuetype={"id": metadata["issueType"]["id"]}
        )
        # Validate local context before sending the create, so corrupt state cannot
        # turn a successful server write into an accidental blind retry.
        should_update = (
            request.update_context
            and request.kind != "Story"
            and self.config.creation.set_context_after_create
            and self.config.context.persist
        )
        if should_update:
            self.store.read()
        result = self.client.write(
            "POST", "issue", expected=201, body={"fields": fields}
        )
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("key"), str)
            or not result.get("id")
        ):
            raise WriteUncertainError(
                "Issue creation response was incomplete; inspect Jira before retrying."
            )
        try:
            key = issue_key(result["key"])
        except UserError as exc:
            raise WriteUncertainError(
                "Issue creation returned an invalid key; inspect Jira before retrying."
            ) from exc
        response: dict[str, Any] = {
            "key": key,
            "url": self.client.url(key),
            "context_updated": False,
        }
        if should_update:
            try:
                self.store.update(Selection(issue=key, project=project))
                response["context_updated"] = True
            except OSError, UserError, UnicodeError:
                # Return the confirmed key even when saving local state fails.
                response["warning"] = (
                    "Issue was created, but local context could not be saved. "
                    "Do not create it again."
                )
        if request.watchers:
            response.update(self.watcher_result(key, request.watchers, created=True))
        return response

    def check_watchers(
        self,
        names: tuple[str, ...],
        *,
        project: str | None = None,
        issue: str | None = None,
    ) -> None:
        current_user = self.current_username()
        if any(name.casefold() != current_user.casefold() for name in names):
            self.permission("MANAGE_WATCHERS", project=project, issue=issue)

    def watch_existing(self, issue: str, names: tuple[str, ...]) -> dict[str, Any]:
        issue = issue_key(issue)
        self.gate("BROWSE_PROJECTS", issue=issue)
        self.check_watchers(names, issue=issue)
        return {
            "key": issue,
            "url": self.client.url(issue),
            **self.watcher_result(issue, names, created=False),
        }

    def watcher_result(
        self, issue: str, names: tuple[str, ...], *, created: bool
    ) -> dict[str, Any]:
        rows = self.add_watchers(issue, names)
        result: dict[str, Any] = {
            "watchers": rows,
            "success": all(row["status"] == "added" for row in rows),
        }
        if not result["success"]:
            result["watcher_warning"] = (
                (
                    f"Issue {issue} was created, but watcher updates are incomplete. "
                    "Do not create it again; "
                    if created
                    else f"Watcher updates for issue {issue} are incomplete. "
                )
                + "Inspect Jira and add remaining watchers to this existing issue. "
                "No automatic retry was sent."
            )
        return result

    def add_watchers(self, issue: str, names: tuple[str, ...]) -> list[dict[str, str]]:
        results: list[dict[str, str]] = []
        stopped = False
        for name in names:
            entry = {"username": name, "status": "not_attempted"}
            if not stopped:
                try:
                    self.client.write(
                        "POST",
                        "issue/" + issue_key(issue) + "/watchers",
                        expected=204,
                        body=name,
                    )
                    entry["status"] = "added"
                except WriteUncertainError as exc:
                    stopped = True
                    entry.update(status="uncertain", error=str(exc))
                except UserError as exc:
                    stopped = True
                    entry.update(status="failed", error=str(exc))
            results.append(entry)
        return results

    def set_field(self, issue: str, name: str | None, value: object) -> dict[str, Any]:
        issue = issue_key(issue)
        self.gate("EDIT_ISSUES", issue=issue)
        specs = self.edit_metadata(issue)
        name = name or self.ask("Field", list(specs))
        key = find_field(name, specs)
        if key in {"status", "project", "issuetype"}:
            raise UserError(
                "Use a workflow transition for status; "
                "project/type moves are outside this command."
            )
        if value is MISSING:
            value = self.ask(str(specs[key].get("name", key)), choices(specs[key]))
        fields = prepare({key: value}, specs, self.ask, required=False)
        self.check_targets(fields, specs, issue=issue)
        self.client.write(
            "PUT", "issue/" + issue, expected=204, body={"fields": fields}
        )
        return {"key": issue, "updated_fields": list(fields)}

    def current_username(self) -> str:
        user = self.client.object("myself")
        name = user.get("name")
        if not isinstance(name, str) or not name.strip() or name == "-1":
            raise UserError("Jira did not return a usable authenticated username.")
        return name

    def assign_self(self, issue: str) -> dict[str, Any]:
        issue = issue_key(issue)
        self.gate("ASSIGN_ISSUES", issue=issue)
        name = self.current_username()
        self.check_assignable(name, issue=issue)
        self.client.write(
            "PUT", "issue/" + issue + "/assignee", expected=204, body={"name": name}
        )
        return {"key": issue, "url": self.client.url(issue), "assigned_to_self": True}

    def set_epic(
        self, issue: str, epic: str | None, *, clear: bool = False
    ) -> dict[str, Any]:
        issue = issue_key(issue)
        self.gate("EDIT_ISSUES", issue=issue)
        specs = self.edit_metadata(issue)
        key = epic_field(specs)
        value = None if clear else issue_key(epic or self.ask("Epic issue key", []))
        fields = prepare({key: value}, specs, self.ask, required=False)
        self.check_targets(fields, specs, issue=issue)
        self.client.write(
            "PUT", "issue/" + issue, expected=204, body={"fields": fields}
        )
        return {"key": issue, "epic": value}

    def transition(
        self, issue: str, name: str | None, values: dict[str, Any]
    ) -> dict[str, Any]:
        issue = issue_key(issue)
        self.gate("TRANSITION_ISSUES", issue=issue)
        data = self.client.object(
            "issue/" + issue + "/transitions", {"expand": "transitions.fields"}
        )
        transitions = data.get("transitions")
        if not isinstance(transitions, list) or any(
            not isinstance(t, dict)
            or not t.get("id")
            or not isinstance(t.get("to", {}), dict)
            for t in transitions
        ):
            raise UserError("Jira returned invalid transition metadata.")
        name = name or self.ask("Transition ID", [str(t["id"]) for t in transitions])
        matches = [
            t
            for t in transitions
            if name in (str(t["id"]), t.get("name"), t.get("to", {}).get("name"))
        ]
        if len(matches) != 1:
            raise UserError(
                "Transition is unavailable or ambiguous; "
                "select an advertised transition ID."
            )
        transition = matches[0]
        specs = transition.get("fields", {})
        if not isinstance(specs, dict) or any(
            not isinstance(v, dict) for v in specs.values()
        ):
            raise UserError("Jira transition fields are not a metadata mapping.")
        fields = prepare(values, specs, self.ask, required=True)
        self.check_targets(fields, specs, issue=issue)
        self.client.write(
            "POST",
            "issue/" + issue + "/transitions",
            expected=204,
            body={"transition": {"id": transition["id"]}, "fields": fields},
        )
        return {"key": issue, "transition": transition["id"]}

    def comment(self, issue: str, body: str) -> dict[str, Any]:
        issue = issue_key(issue)
        if not body.strip():
            raise UserError("Comment text must not be empty.")
        self.gate("ADD_COMMENTS", issue=issue)
        result = self.client.write(
            "POST", "issue/" + issue + "/comment", expected=201, body={"body": body}
        )
        if not isinstance(result, dict) or not result.get("id"):
            raise WriteUncertainError(
                "Comment response was incomplete; inspect Jira before retrying."
            )
        return {"key": issue, "comment_id": result["id"]}

    def attach(self, issue: str, paths: list[Path]) -> dict[str, Any]:
        issue = issue_key(issue)
        self.gate("CREATE_ATTACHMENTS", issue=issue)
        metadata = self.client.object("attachment/meta")
        limit = metadata.get("uploadLimit")
        if metadata.get("enabled") is not True or type(limit) is not int or limit <= 0:
            raise UserError(
                "Jira attachments are disabled or their upload limit is unavailable."
            )
        if not paths or any(
            not path.is_file() or path.stat().st_size > limit for path in paths
        ):
            raise UserError(
                "Select regular local files within Jira's advertised upload limit."
            )
        if len({path.resolve() for path in paths}) != len(paths):
            raise UserError("Select each local file only once.")
        results: list[dict[str, Any]] = []
        stopped = False
        for path in paths:
            entry: dict[str, Any] = {"file": path.name, "status": "not_attempted"}
            if not stopped:
                try:
                    with path.open("rb") as stream:
                        uploaded = self.client.write(
                            "POST",
                            "issue/" + issue + "/attachments",
                            expected=200,
                            files={
                                "file": (path.name, stream, "application/octet-stream")
                            },
                        )
                    if (
                        not isinstance(uploaded, list)
                        or len(uploaded) != 1
                        or not isinstance(uploaded[0], dict)
                        or not uploaded[0].get("id")
                    ):
                        raise WriteUncertainError(
                            "Attachment response was incomplete; "
                            "inspect Jira before retrying."
                        )
                    entry.update(status="uploaded", id=uploaded[0]["id"])
                except WriteUncertainError as exc:
                    stopped = True
                    entry.update(status="uncertain", error=str(exc))
                except (UserError, OSError) as exc:
                    stopped = True
                    entry.update(
                        status="failed",
                        error=str(exc)
                        if isinstance(exc, UserError)
                        else "Cannot read the selected file.",
                    )
            results.append(entry)
        return {"key": issue, "success": not stopped, "files": results}


MISSING = object()
