"""Generate local PowerShell 7 completion from the authoritative argument parser."""

import argparse
import json
from typing import Any


def tree(parser: argparse.ArgumentParser) -> dict[str, Any]:
    children = {}
    options = {}
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            children.update(
                {name: tree(child) for name, child in action.choices.items()}
            )
        for option in action.option_strings:
            options[option] = {
                "value": action.nargs != 0,
                "choices": list(action.choices) if action.choices is not None else [],
            }
    return {"children": children, "options": options}


def powershell(parser: argparse.ArgumentParser) -> str:
    data = json.dumps(tree(parser), ensure_ascii=True).replace("'", "''")
    return (
        """# PowerShell 7; static metadata only, no Jira requests or credential reads.
Register-ArgumentCompleter -Native -CommandName jira -ScriptBlock {
    param($wordToComplete, $commandAst, $cursorPosition)
    $node = '"""
        + data
        + """' | ConvertFrom-Json -AsHashtable
    $pending = $null
    foreach ($element in @($commandAst.CommandElements | Select-Object -Skip 1)) {
        if ($element.Extent.EndOffset -ge $cursorPosition) { break }
        $text = $element.Extent.Text
        if ($null -ne $pending) { $pending = $null; continue }
        if ($node.options.ContainsKey($text)) {
            if ($node.options[$text].value) { $pending = $node.options[$text] }
        } elseif ($node.children.ContainsKey($text)) {
            $node = $node.children[$text]
        }
    }
    $candidates = if ($null -ne $pending) { @($pending.choices) }
        else { @($node.children.Keys) + @($node.options.Keys) }
    $candidates | Sort-Object -Unique |
        Where-Object {
            $_.StartsWith($wordToComplete, [StringComparison]::OrdinalIgnoreCase)
        } |
        ForEach-Object {
            [System.Management.Automation.CompletionResult]::new(
                $_, $_, 'ParameterValue', $_)
        }
}
"""
    )
