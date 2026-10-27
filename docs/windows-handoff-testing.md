# S05 Windows handoff refinement verification

This checklist belongs to existing S05 / R1. Automated results use synthetic
fixtures and do not prove corporate Jira integration. Record corporate outcomes
separately in authorized corporate storage; never upload PATs, personal details,
or raw corporate Jira responses to this public repository.

## Install the build to test

Use a source checkout containing these changes, Python 3.14, approved uv, and
PowerShell 7. Record `git rev-parse HEAD`. Build and reinstall a fresh wheel using
the README's corporate certificate trust instructions. Check `Get-Command jira`
resolves that installation. Existing configuration and PAT remain in
`%APPDATA%\jira-cli\config.yaml` and `%APPDATA%\jira-cli\.env`.

## Read-only checks first

Connect to the authorized corporate network. Replace example keys with existing
accessible Story/Epic keys; do not create new issues for this phase.

```powershell
jira auth test --project WWRAP
jira doctor --project WWRAP
jira story view WWRAP-123
jira epic view WWRAP-100
jira issue view WWRAP-123
jira story list
jira epic list
jira my today
```

Compare with Jira's UI: key/URL/summary/type, configured priority and status names,
assignee display name/email availability, and direct Epic association. Check a
known `Last, First` name is reordered only when unambiguous. Missing assignees or
hidden email addresses must not fail. No avatar/icon URLs should appear by default.
Check `--format json` uses the same normalized names and fields. Check `--detail`
adds business details rather than raw REST envelopes.

`story view` must reject a known Epic; `epic view` must reject a known Story;
generic `issue view` must accept both. Test context using an existing selection:
view without a key and confirm `context show` is unchanged. Explicit `--issue`
must override context without changing it. Epic `--all-stories` should include
other assignees where permitted, while personal counts remain personal.

## Verify filters without modifying Jira

Inspect `jira config show` locally to confirm the intended project, configured
`defaults.filters`, and presentation settings. Under the default configuration:

```powershell
jira story list --project WWRAP --format json
jira issue list --project WWRAP --format json
jira story list --mine --include-done --project WWRAP --format json
jira story list --all --project WWRAP --format json
jira epic list --project WWRAP --format json
jira epic list --with-my-stories --project WWRAP --format json
jira epic list --mine --include-done --project WWRAP --format json
```

Default Issue/Story lists should contain your non-Done assignments. `--all`
includes other assignees and Done issues; `--include-done` alone retains your
assignment filter. Custom category defaults use category metadata, not workflow
status names. An explicit view of an accessible unassigned/Done issue must still
work. Explicit `search --jql` must execute exactly the supplied query.

For Epic relevance, use existing known examples if available: an assigned open
Epic; an Epic assigned to someone else with your active Story; and a completed
Epic with your active Story. The latter two must remain relevant, with no duplicates
and with the completed-Epic marker/counts visible. Do not change live assignees or
statuses merely to manufacture test cases.

Optionally use `story select` / `epic select` in an interactive terminal. They
change only local active context. Story selection defaults to your unresolved
active Stories; Epic selection uses relevance. Blank input must leave context
unchanged. Noninteractive/JSON/quiet pickers should fail clearly without prompting.

## Inline descriptions: syntax first, controlled writes only after authorization

This conflicting-source test must fail with exit code 2 before contacting Jira:

```powershell
jira create story --description 'Inline text' --description-file '.\requirements.md'
```

Do not execute the following creation example unless you have separate explicit
authorization to create a designated test issue in the selected project. Check
required fields using metadata first and provide any additional values through
the existing `--field NAME=VALUE` interface. Use a clearly identified test issue
and follow the corporate cleanup process manually.

```powershell
$description = @'
First authorized test line with "quotes" and an apostrophe's value.
Second authorized test line with a literal `backtick` and spaces.
'@
jira create story --project WWRAP --summary 'Authorized CLI description test' --description $description --no-context --non-interactive
```

Inspect the returned server-assigned key in Jira and compare the full description,
including line breaks. `--no-context` avoids changing local selection during this
controlled test. Separately authorized Epic testing uses `create epic` with the
same description options and all metadata-required fields (such as Epic Name).
The default context-after-confirmed-creation behavior is unchanged and covered by
automated tests. Retain the existing description-file path as an alternative.

If a write outcome is uncertain, inspect Jira before any manual retry. Do not run
automatic writes, bulk updates, or test-issue cleanup scripts.

## Record outcomes separately

Record the tested Git revision, command, exit code, sanitized observed result, and
whether the result came from mocked tests, Windows CI, or the actual corporate
instance. Track certificate/proxy policy, priority/category metadata availability,
Epic Link schema, display-name formats, hidden email behavior, and required custom
fields as corporate verification items. A mocked test pass must never be recorded
as Corporate Integration Verified.
