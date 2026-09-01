---
name: ce-resolve-pr-feedback
description: Resolve PR feedback by implementing requested changes. Tracks feedback items and ensures all are addressed before merge.
---

# /ce-resolve-pr-feedback

## Description

Resolve PR feedback by implementing requested changes. Tracks feedback items and ensures all are addressed before merge.

## When to Use

- After receiving PR review feedback
- When addressing review comments
- Before `/ce-babysit-pr` to resolve outstanding feedback
- When iterating on PR changes

## Input

- **pr-url**: URL of the PR with feedback
- **feedback**: Specific feedback to address (optional)
- **auto-commit**: Whether to auto-commit changes

## Output

- **Code**: Changes addressing feedback
- **File**: `docs/pr-feedback/resolve-{timestamp}.md`

## Configuration

```yaml
ce-resolve-pr-feedback:
  auto-commit: false  # Whether to auto-commit changes
  commit-message: "fix: address PR feedback"  # Default commit message
  resolve-all: true  # Whether to resolve all feedback
```

## allowed-tools

- Bash: `git commit`, `gh pr view`, `gh pr diff`
- Read: Read code and feedback
- Write: Modify code
- Grep: Search for patterns

## Prompt

```
<resolve-feedback>
Resolve PR feedback for: {{pr-url}}

<feedback-resolution>
1. Fetch PR feedback and comments
2. Categorize feedback (required, suggested, question)
3. For each feedback item:
   - Understand the request
   - Implement the change
   - Verify the change addresses the feedback
4. Commit changes if auto-commit is enabled
5. Report resolution status
</feedback-resolution>

<feedback-tracking>
Track each feedback item:
- Status: pending → in-progress → resolved → verified
- Type: required | suggested | question
- Resolution: how it was addressed
- Evidence: proof that feedback was addressed
</feedback-tracking>
</resolve-feedback>

<output-format>
Feedback resolution report:
```
docs/pr-feedback/resolve-{timestamp}.md
├── pr-url: <PR URL>
├── feedback-items:
│   ├── {n}:
│   │   ├── comment: <feedback text>
│   │   ├── type: <required|suggested|question>
│   │   ├── status: <pending|in-progress|resolved|verified>
│   │   ├── resolution: <how it was addressed>
│   │   └── evidence: <proof of resolution>
│   └── ...
├── summary: {resolved}/{total} items resolved
└── next-steps: <what to do next>
```
</output-format>

## Examples

### Basic feedback resolution:
```
/ce-resolve-pr-feedback
```

### Resolve specific PR:
```
/ce-resolve-pr-feedback --pr-url https://github.com/org/repo/pull/123
```

### Auto-commit changes:
```
/ce-resolve-pr-feedback --auto-commit
```

## Notes

- Integrates with /ce-babysit-pr for continuous monitoring
- Tracks feedback resolution status
- Auto-commit can be enabled for faster iteration
- Stored in docs/pr-feedback/ for reference