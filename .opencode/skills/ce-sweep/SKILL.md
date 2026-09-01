---
name: ce-sweep
description: Sweep feedback sources (issues, PRs, support tickets, user research) and track item lifecycles. Emits /lfg-ready plan when enough feedback accumulates.
---

# /ce-sweep

## Description

Sweep feedback sources (issues, PRs, support tickets, user research) and track item lifecycles. Emits `/lfg`-ready plan when enough feedback accumulates.

## When to Use

- Periodically to gather feedback
- Before `/ce-brainstorm` to ground ideas in user feedback
- When accumulating feedback for a new feature
- After major releases to collect user input

## Input

- **sources**: Feedback sources to sweep (issues, PRs, support, research)
- **labels**: Labels to filter by
- **time-window**: Time window for feedback

## Output

- **File**: `docs/feedback/sweeps/sweep-{timestamp}.md`
- **Plan**: `/lfg`-ready plan if enough feedback accumulated

## Configuration

```yaml
ce-sweep:
  sources: ["issues", "prs", "support"]  # Default sources
  time-window: "30d"  # Default time window
  min-feedback-for-plan: 5  # Min feedback items to trigger plan
  auto-create-plan: false  # Whether to auto-create /lfg plan
```

## allowed-tools

- Bash: `gh issue list`, `gh pr list`, API calls
- Read: Read issue/PR details
- Grep: Search for patterns in feedback
- Glob: Find feedback files

## Prompt

```
<feedback-sweep>
Sweep feedback sources and track item lifecycles.

<sweep-process>
1. Collect feedback from all configured sources
2. Categorize feedback (bug, feature, question, praise)
3. Track lifecycle (new → acknowledged → in-progress → resolved)
4. Identify patterns and themes
5. If enough feedback accumulated, emit /lfg-ready plan
</sweep-process>

<source-types>
- Issues: GitHub issues, bug reports
- PRs: Pull request feedback, comments
- Support: Support tickets, user emails
- Research: User research notes, interviews
- Reviews: App store reviews, survey responses
</source-types>

<feedback-categorization>
For each feedback item:
- Type: bug | feature | question | praise
- Priority: P0-P3
- Status: new | acknowledged | in-progress | resolved | wontfix
- Theme: <identified theme>
- Related: <related issues/PRs>
</feedback-categorization>
</feedback-sweep>

<output-format>
Feedback sweep report:
```
docs/feedback/sweeps/sweep-{timestamp}.md
├── sources: <list of sources swept>
├── time-window: <time period>
├── feedback-items:
│   ├── {n}:
│   │   ├── source: <where it came from>
│   │   ├── type: <bug|feature|question|praise>
│   │   ├── priority: <P0-P3>
│   │   ├── status: <new|acknowledged|in-progress|resolved|wontfix>
│   │   ├── theme: <identified theme>
│   │   └── summary: <brief description>
│   └── ...
├── themes:
│   ├── {theme}: <count> items
│   └── ...
├── lfg-ready-plan: <if enough feedback accumulated>
└── action-items: <next steps>
```
</output-format>

## Examples

### Basic sweep:
```
/ce-sweep
```

### Sweep specific sources:
```
/ce-sweep --sources issues,research
```

### Sweep with time window:
```
/ce-sweep --time-window 7d
```

### Auto-create plan:
```
/ce-sweep --auto-create-plan
```

## Notes

- Integrates with /ce-brainstorm for idea grounding
- Can emit /lfg-ready plans when enough feedback accumulates
- Stored in docs/feedback/sweeps/ for reference
- Helps close the feedback loop between users and development