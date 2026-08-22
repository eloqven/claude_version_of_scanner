---
name: ce-handoff
description: Session handoff at temp store. Allows resuming work from a selected source, enabling collaboration across sessions or team members.
---

# /ce-handoff

## Description

Session handoff at temp store. Allows resuming work from a selected source, enabling collaboration across sessions or team members.

## When to Use

- When handing off work to another team member
- When resuming work from a previous session
- When switching between machines
- When pausing and resuming long-running work

## Input

- **source**: Source to handoff from (session, branch, PR)
- **destination**: Where to handoff to (session, file, PR)
- **include**: What to include (code, docs, context)

## Output

- **File**: `docs/handoffs/handoff-{timestamp}.md`
- **State**: Saved session state for resumption

## Configuration

```yaml
ce-handoff:
  temp-store: "local"  # local | remote | cloud
  include-context: true  # Include conversation context
  include-docs: true  # Include generated docs
  include-code: true  # Include code changes
```

## allowed-tools

- Bash: `git stash`, `git diff`, file operations
- Read: Read session state
- Write: Save handoff state

## Prompt

```
<handoff-process>
Create a handoff package for resuming work.

<handoff-contents>
1. **Context** - Current state, goals, and blockers
2. **Code** - Stashed or committed changes
3. **Docs** - Generated documents and notes
4. **Plan** - Current plan and progress
5. **Environment** - Setup instructions and dependencies
6. **Next Steps** - What to do when resuming
</handoff-contents>

<handoff-formats>
- Session: Save to opencode session for later resume
- File: Save to docs/handoffs/ for team sharing
- PR: Create draft PR with changes and context
</handoff-formats>
</handoff-process>

<output-format>
Handoff package:
```
docs/handoffs/handoff-{timestamp}.md
├── source: <session/branch/PR>
├── context:
│   ├── current-goal: <what we're working on>
│   ├── progress: <what's done>
│   ├── blockers: <what's blocked>
│   └── decisions: <key decisions made>
├── code-changes: <stashed or committed changes>
├── docs: <generated documents>
├── plan: <current plan and U-ID status>
├── environment:
│   ├── dependencies: <installed packages>
│   ├── setup: <setup instructions>
│   └── tools: <required tools>
└── next-steps: <what to do when resuming>
```
</output-format>

## Examples

### Basic handoff:
```
/ce-handoff
```

### Handoff to file:
```
/ce-handoff --destination file
```

### Handoff with context:
```
/ce-handoff --include-context --include-docs
```

## Notes

- Enables collaboration across sessions and team members
- Handoff packages stored in docs/handoffs/
- Can resume from handoff with /ce-work --resume
- Integrates with /lfg for autonomous handoff