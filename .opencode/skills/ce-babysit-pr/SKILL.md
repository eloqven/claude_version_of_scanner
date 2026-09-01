---
name: ce-babysit-pr
description: Watch an open PR (or confirmed managed stack under posture) and keep it moving toward merge, reacting to review comments and CI as they arrive.
---

# /ce-babysit-pr

## Description

Watch an open PR (or confirmed managed stack under posture) and keep it moving toward merge, reacting to review comments and CI as they arrive.

## When to Use

- After `/ce-commit-push-pr` creates a PR
- When you want hands-off PR monitoring
- When CI is flaky and needs retry management
- When review comments need addressing

## Input

- **pr-url**: URL of the PR to babysit (defaults to most recent)
- **auto-fix**: Whether to auto-fix CI failures
- **auto-respond**: Whether to auto-respond to review comments

## Output

- **Monitoring**: Continuous PR status monitoring
- **Actions**: Auto-fixes, responses, and updates
- **File**: `docs/prs/babysit-{pr-number}-{timestamp}.log`

## Configuration

```yaml
ce-babysit-pr:
  check-interval: "60s"  # How often to check PR status
  auto-merge: false  # Whether to auto-merge when ready
  auto-fix-ci: true  # Auto-fix common CI failures
  max-retries: 3  # Max CI retry attempts
  comment-on-changes: true  # Comment when changes are pushed
```

## allowed-tools

- Bash: `git push`, `git commit`, `gh pr view`, `gh pr checks`
- Read: Read PR comments and CI logs
- Grep: Search for CI failure patterns

## Prompt

```
<pr-babysitting>
Monitor PR {{pr-url}} and keep it moving toward merge.

<babysitting-process>
1. Check PR status (CI, reviews, conflicts)
2. If CI fails:
   - Analyze failure cause
   - If auto-fixable, apply fix and push
   - If not, report and wait for human intervention
3. If review comments:
   - Address comments with code changes
   - Respond to comments with explanations
4. If conflicts:
   - Attempt to resolve
   - If complex, report for human intervention
5. If ready to merge:
   - Verify all checks pass
   - Merge if auto-merge is enabled
</babysitting-process>

<ci-monitoring>
Monitor CI status and react:
- Green: Proceed to merge check
- Red: Analyze failure, attempt auto-fix
- Pending: Wait and recheck
- Flaky: Retry up to max-retries
</ci-monitoring>
</pr-babysitting>

<output-format>
Babysitting log:
```
docs/prs/babysit-{pr-number}-{timestamp}.log
├── start-time: <timestamp>
├── end-time: <timestamp>
├── actions:
│   ├── {timestamp}: {action taken}
│   └── ...
├── ci-results:
│   ├── {check-name}: {status}
│   └── ...
├── review-comments:
│   ├── {comment}: {response}
│   └── ...
└── final-status: {merged|closed|needs-human}
```
</output-format>

## Examples

### Basic babysit:
```
/ce-babysit-pr
```

### Babysit specific PR:
```
/ce-babysit-pr --pr-url https://github.com/org/repo/pull/123
```

### Auto-merge when ready:
```
/ce-babysit-pr --auto-merge
```

### No auto-fix:
```
/ce-babysit-pr --no-auto-fix-ci
```

## Notes

- Runs continuously until PR is merged or closed
- Auto-fixes common CI failures (linting, formatting, test flakes)
- Comments on PR when changes are pushed
- Integrates with `/ce-commit-push-pr` for seamless workflow