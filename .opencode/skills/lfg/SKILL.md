---
name: lfg
description: Full autonomous engineering workflow. Runs the entire loop hands-off: plan, work, review, compound, commit, PR, CI repair until green.
---

# /lfg

## Description

Full autonomous engineering workflow. Runs the entire loop hands-off: plan → work → review → compound → commit → PR → CI repair until green.

## When to Use

- When you want to step away and come back to a green PR
- After `/ce-brainstorm` so it plans against real requirements
- For well-defined features with clear requirements
- When you trust the agent to handle the full workflow

## Input

- **feature**: The feature to implement (from /ce-brainstorm)
- **plan-file**: Path to the plan file (optional)
- **max-time**: Maximum time to run (default: 2h)

## Output

- **Code**: Implemented feature
- **PR**: Open pull request with green CI
- **File**: `docs/lfg/lfg-{timestamp}.md` (log)

## Configuration

```yaml
lfg:
  max-time: "2h"  # Maximum execution time
  auto-merge: false  # Auto-merge when CI is green
  notify-on-complete: true  # Notify when done
  repair-ci: true  # Auto-repair CI failures
  max-ci-retries: 3  # Max CI retry attempts
```

## allowed-tools

- All tools (Bash, Read, Write, Grep, Glob, Task)

## Prompt

```
<autonomous-workflow>
Run the full engineering workflow hands-off:

1. **Plan** - Read /ce-brainstorm output, create /ce-plan
2. **Work** - Execute plan with /ce-work
3. **Simplify** - Run /ce-simplify-code on changes
4. **Review** - Run /ce-code-review, apply fixes
5. **Compound** - Capture learnings with /ce-compound
6. **Commit** - Create commit with /ce-commit
7. **Push** - Push to remote branch
8. **PR** - Open PR with /ce-commit-push-pr
9. **Babysit** - Monitor CI with /ce-babysit-pr
10. **Repair** - Fix CI failures until green

<workflow-guardrails>
- Only proceed to next step when current step is complete
- If blocked, document blocker and continue with next U-ID
- If CI fails, attempt auto-repair up to max-retries
- If max-time exceeded, stop and report current state
- All changes must pass /ce-code-review before PR
</workflow-guardrails>

<eligible-work>
If plan has unplanned work after completion:
- Recommend next separately planned area
- Justify why it should be separate
- Only create /ce-handoff if user accepts
</eligible-work>
</autonomous-workflow>

<output-format>
LFG log:
```
docs/lfg/lfg-{timestamp}.md
├── start-time: <timestamp>
├── end-time: <timestamp>
├── steps:
│   ├── plan: {status} - {duration}
│   ├── work: {status} - {duration}
│   ├── simplify: {status} - {duration}
│   ├── review: {status} - {duration}
│   ├── compound: {status} - {duration}
│   ├── commit: {status} - {duration}
│   ├── push: {status} - {duration}
│   ├── pr: {status} - {duration}
│   └── babysit: {status} - {duration}
├── ci-results:
│   ├── {check}: {status}
│   └── ...
├── final-status: {success|partial|failed}
└── pr-url: {url}
```
</output-format>

## Examples

### Full autonomous workflow:
```
/lfg
```

### With time limit:
```
/lfg --max-time 1h
```

### Auto-merge when green:
```
/lfg --auto-merge
```

## Notes

- Start with /ce-brainstorm so LFG plans against real requirements
- LFG is the autopilot version of the standard loop
- Comes back to an open, green PR
- If eligible multi-area plan has unplanned work, recommends next area
- Only creates /ce-handoff if user accepts the recommendation