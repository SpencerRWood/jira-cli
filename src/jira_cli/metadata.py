"""Validate writable values using the target instance's field metadata."""

import json
import math
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from jira_cli.config import UserError, issue_key
from jira_cli.views import EPIC_NAME_SCHEMA

type Fields = dict[str, dict[str, Any]]
type Ask = Callable[[str, list[str]], str]


def field_map(values: object) -> Fields:
    if not isinstance(values, list):
        raise UserError("Jira creation metadata is not a field list.")
    result: Fields = {}
    for value in values:
        if not isinstance(value, dict) or not isinstance(value.get("fieldId"), str):
            raise UserError("Jira field metadata is missing a field ID.")
        if value["fieldId"] in result:
            raise UserError("Jira field metadata contains duplicate field IDs.")
        result[value["fieldId"]] = value
    return result


def find_field(name: str, specs: Fields) -> str:
    if name == "epic_name":
        matches = [
            key
            for key, spec in specs.items()
            if isinstance(spec.get("schema"), dict)
            and spec["schema"].get("custom") == EPIC_NAME_SCHEMA
        ]
        if len(matches) == 1:
            return matches[0]
        if matches:
            raise UserError("Epic Name creation metadata is ambiguous.")
        name = "Epic Name"
    if name in specs:
        return name
    matches = [
        key
        for key, spec in specs.items()
        if str(spec.get("name", "")).casefold() == name.casefold()
    ]
    if len(matches) != 1:
        raise UserError(
            "Field is unavailable or ambiguous; use a writable metadata field ID."
        )
    return matches[0]


def choices(spec: dict[str, Any]) -> list[str]:
    allowed = spec.get("allowedValues", [])
    if not isinstance(allowed, list):
        raise UserError("Jira field allowedValues must be a list.")
    return [
        str(value.get("name", value.get("value", value.get("id", ""))))
        for value in allowed
        if isinstance(value, dict)
    ]


def allowed_value(value: object, spec: dict[str, Any], kind: str) -> object:
    allowed = spec.get("allowedValues", [])
    if not isinstance(allowed, list):
        raise UserError("Jira field allowedValues must be a list.")
    if allowed:
        matches = [
            item
            for item in allowed
            if isinstance(item, dict)
            and (
                value == item
                or (
                    isinstance(value, str)
                    and value
                    in [
                        str(item.get(k))
                        for k in ["id", "name", "value", "key", "displayName"]
                        if item.get(k) is not None
                    ]
                )
                or (
                    isinstance(value, dict)
                    and value
                    and all(item.get(k) == v for k, v in value.items())
                )
            )
        ]
        if len(matches) != 1:
            raise UserError("Value is not an unambiguous allowed field value.")
        field = "name" if kind in {"user", "group"} else "id"
        if field not in matches[0]:
            raise UserError("Jira allowed field value lacks a canonical identifier.")
        return {field: matches[0][field]}
    if kind == "user":
        if isinstance(value, str) and value:
            return {"name": value}
        if (
            isinstance(value, dict)
            and set(value) == {"name"}
            and isinstance(value["name"], str)
            and value["name"]
        ):
            return value
    raise UserError(
        "Field requires supported allowedValues metadata; no value was guessed."
    )


def text_value(value: object, spec: dict[str, Any]) -> str:
    if not isinstance(value, str) or (spec.get("required") and not value.strip()):
        raise UserError("Expected a nonempty text field value.")
    kind = spec["schema"]["type"]
    allowed = spec.get("allowedValues", [])
    if not isinstance(allowed, list):
        raise UserError("Jira field allowedValues must be a list.")
    if allowed and not any(
        value == choice
        or (
            isinstance(choice, dict)
            and value in (choice.get("value"), choice.get("name"), choice.get("id"))
        )
        for choice in allowed
    ):
        raise UserError("Text value is not an advertised allowed field value.")
    try:
        if kind == "date":
            date.fromisoformat(value)
        if kind == "datetime":
            datetime.fromisoformat(value)
    except ValueError as exc:
        raise UserError("Expected an ISO date or datetime field value.") from exc
    return value


def number_value(value: object) -> float:
    try:
        number = float(str(value))
    except ValueError as exc:
        raise UserError("Expected a numeric field value.") from exc
    if not math.isfinite(number):
        raise UserError("Expected a finite numeric field value.")
    return number


def boolean_value(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value in {"true", "false"}:
        return value == "true"
    raise UserError("Expected true or false for a boolean field.")


def array_value(value: object, spec: dict[str, Any]) -> list[object]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError as exc:
            raise UserError("Array fields require a JSON array.") from exc
    if not isinstance(value, list):
        raise UserError("Array fields require a JSON array.")
    if spec.get("required") and not value:
        raise UserError("A required array field cannot be empty.")
    item_spec = {
        **spec,
        "schema": {"type": spec["schema"].get("items")},
        "required": False,
    }
    return [coerce(item, item_spec) for item in value]


def coerce(value: object, spec: dict[str, Any]) -> object:
    operations = spec.get("operations", ["set"])
    if not isinstance(operations, list) or "set" not in operations:
        raise UserError("The field does not permit a set operation.")
    if value is None:
        if spec.get("required"):
            raise UserError("A required field cannot be cleared.")
        return None
    schema = spec.get("schema", {})
    if not isinstance(schema, dict):
        raise UserError("Jira field schema is not a metadata object.")
    kind = schema.get("type")
    if not isinstance(kind, str):
        raise UserError("Jira field schema must advertise a supported type.")
    if schema.get("custom") == "com.pyxis.greenhopper.jira:gh-epic-link":
        if not isinstance(value, str):
            raise UserError("Epic Link requires an issue key.")
        return issue_key(value)
    if kind in {"string", "date", "datetime"}:
        return text_value(value, spec)
    if kind in {"number", "boolean", "array"}:
        return typed_value(value, spec)
    if kind in {
        "option",
        "priority",
        "user",
        "group",
        "version",
        "component",
        "resolution",
    }:
        return allowed_value(value, spec, kind)
    raise UserError(
        "Unsupported field schema; inspect metadata rather than guessing its shape."
    )


def typed_value(value: object, spec: dict[str, Any]) -> object:
    kind = spec["schema"]["type"]
    if kind == "number":
        return number_value(value)
    if kind == "boolean":
        return boolean_value(value)
    return array_value(value, spec)


def prepare(
    values: dict[str, Any], specs: Fields, ask: Ask, *, required: bool
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in values.items():
        key = find_field(name, specs)
        if key in result:
            raise UserError("The same field was specified more than once.")
        result[key] = coerce(value, specs[key])
    if required:
        for key, spec in specs.items():
            if (
                key not in result
                and spec.get("required")
                and not spec.get("hasDefaultValue")
            ):
                result[key] = coerce(
                    ask(str(spec.get("name", key)), choices(spec)), spec
                )
    return result


def epic_field(specs: Fields) -> str:
    if any(not isinstance(spec.get("schema", {}), dict) for spec in specs.values()):
        raise UserError("Jira field schema is not a metadata object.")
    matches = [
        key
        for key, spec in specs.items()
        if spec.get("schema", {}).get("custom")
        == "com.pyxis.greenhopper.jira:gh-epic-link"
    ]
    if len(matches) != 1:
        raise UserError(
            "No unambiguous writable Data Center Epic Link field was advertised."
        )
    return matches[0]
