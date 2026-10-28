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
$story = Read-Host 'Authorized existing Story key'
$epic = Read-Host 'Authorized existing Epic key'
jira context set $story
jira context show
jira story view $story
jira story view $story --detail
jira epic view $epic
jira epic view $epic --detail
jira issue view WWRAP-123
jira story list
jira epic list
jira my today
```

`my today` defaults to staleness sorting (oldest update first) and excludes Epics.
Its issue table includes Key, Summary, Status, Priority, Due Date,
Updated, Latest Comment and Attention columns. The latest accessible comment
includes the author's display name, timestamp and multiline text; absent comments
show `—`. Comments are requested with the issue search, with a latest-comment
request only when Jira returns an incomplete comment history. Compare the latest
comment against Jira's UI, including an issue with a long history and any comments
restricted from the PAT user. Verify blockers and inferred risks are readable
text, missing values show `—`, and the scope/sort header matches the command.
Check `jira my today --project WWRAP --sort priority --format table` and compare
its rows/order with `--format json`. `--quiet` must suppress all output.

Compare with Jira's UI: key/URL/summary/type, configured priority and status names,
assignee display name/email availability, and direct Epic association. Check a
known `Last, First` name is reordered only when unambiguous. Missing assignees or
hidden email addresses must not fail. No avatar/icon URLs should appear by default.
Check `--format json` uses the same normalized names and fields, including
`epic_key` and `epic_name`. Story lists show Epic Key and Epic Name; absent or
inaccessible epics have empty names. Epic lists show exactly Key, Epic Name,
Summary, Status, Priority, Assignee, My Active Stories, in that order.

Views use a wrapping Field/Value table with the latest accessible comment.
The default Story view also shows Description, preserving line breaks and wrapping
long text; a missing description shows `—`.
Detailed views retain those fields, add the full description, created/updated
dates, attachment filenames and related issue keys, and show one comment per row,
newest first with multiline content. Check a paged history against Jira's UI;
only comments accessible to the PAT user may appear. Detailed views default to
20 comments; `jira config set output.comment_limit 50` changes that limit in the
existing config.yaml (valid range 1–1000). Default views still show one comment.
Epic details have a separate child Story table honoring `--mine`/`--all-stories`.

Context set/show displays project, issue, summary, status and parent epic key/name.
Check an existing issue whose association was changed through an independently
authorized Jira workflow: `context show` must reflect the current association.
Do not change an issue merely for this check. With connectivity unavailable,
saved context remains usable and output clearly reports unavailable live data.

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
and with the active Story counts visible. Do not change live assignees or
statuses merely to manufacture test cases.

An assigned Epic in the Done category without active personal Stories is excluded,
even if its resolution is empty. `--mine --include-done` explicitly broadens that
filter. The selected project still applies.
Set an existing Epic as local context and verify Story scoping read-only:

```powershell
jira context set WWRAP-100
jira story list
jira story list --all
jira story list --no-epic
jira context show
```

Scoped table output should start with `Epic context: WWRAP-100`. Compare the listed
Stories' Epic Link in Jira; `--all` retains the Epic scope, while `--no-epic`
uses the normal project list. JSON and quiet output omit the notice.

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
including line breaks. Confirm the default assignee is your authenticated Jira
account and that `jira context show` remains unchanged. Story creation always
preserves context; `--no-context` remains valid but is unnecessary for Stories.
If an Epic is active, confirm the new Story's Epic Link matches it. Use an explicit
`--assignee` only when authorized to test that override.
Separately authorized Epic testing uses a distinct native Epic Name and Summary:

```powershell
# Execute only after approval of this test case and project.
jira create epic --project WWRAP `
  --epic-name 'Authorized CLI Test Epic' `
  --summary 'Approved test of distinct name and summary' `
  --description $description --non-interactive
```

Provide any other metadata-required fields. Verify the native Epic Name custom
field and Summary independently in Jira, plus its server-allocated key.
Interactive creation prompts for required Epic Name when omitted. YAML creation
accepts `epic_name: Authorized CLI Test Epic` alongside `summary:`; existing
`Epic Name`/custom field ID definitions remain supported. No Summary-to-name
copy occurs. `--description-file` and inline `--description` remain alternatives.
Epic context-after-confirmed-creation behavior is unchanged. Retain the existing
description-file path as an alternative.

Only with separate authorization for test-issue creation and watcher changes,
add `--watchers 'username1,username2'` using approved test accounts. Verify the
watcher list in Jira and confirm the CLI reports each requested user as `added`.
Do not manufacture permission failures on live issues. If the issue was created
but watcher updates fail, retain its returned key and inspect that issue; do not
rerun creation. Watcher permissions, global watching settings, and user access
require corporate validation independently of mocked test results.

For a separately authorized watcher-only test on an existing issue, use
`jira watcher add --issue WWRAP-123 --watchers 'username1,username2'`. Confirm in
Jira that the specified users watch that existing issue, no issue was created,
and local context is unchanged. Omitting `--issue` targets active context.

If a write outcome is uncertain, inspect Jira before any manual retry. Do not run
automatic writes, bulk updates, or test-issue cleanup scripts.

## Record outcomes separately

Record the tested Git revision, command, exit code, sanitized observed result, and
whether the result came from mocked tests, Windows CI, or the actual corporate
instance. Track certificate/proxy policy, priority/category metadata availability,
Epic Link schema, display-name formats, hidden email behavior, and required custom
fields as corporate verification items. A mocked test pass must never be recorded
as Corporate Integration Verified.

Corporate compatibility questions still pending: the instance's Epic Link and
Epic Name schema metadata, project-specific creation screens/required fields,
comment pagination and `orderBy=-created` behavior, hidden comment permissions,
and terminal wrapping in the approved PowerShell/VS Code font and width.
