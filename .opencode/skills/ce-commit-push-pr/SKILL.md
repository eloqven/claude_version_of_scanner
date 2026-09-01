---
name: ce-commit-push-pr
description: Commit, push, and open a PR (or construct/submit an opt-in managed stack) that teaches any concept the change newly introduces.
---

# /ce-commit-push-pr

## Description

Commit, push, and open a PR (or construct/submit an opt-in managed stack) that teaches any concept the change newly introduces.

## When to Use

- After `/ce-code-review` approves changes
- When you're ready to share work with the team
- Before `/ce-babysit-pr` to watch the PR
- When you need a PR that explains new concepts

## Input

- **branch**: Branch to commit and push (defaults to current)
- **pr-title**: Title for the PR
- **pr-description**: Description for the PR
- **reviewers**: List of reviewers to assign

## Output

- **Git operations**: Commit, push, branch creation
- **PR**: Created pull request with description
- **File**: `docs/prs/pr-{timestamp}.md` (optional log)

## Configuration

```yaml
ce-commit-push-pr:
  auto-assign-reviewers: true  # Auto-assign based on CODEOWNERS
  pr-template: "standard"  # standard | minimal | detailed
  teach-concepts: true  # Whether to explain new concepts in PR
  draft-if-dirty: false  # Create draft PR if work is incomplete
```

## allowed-tools

- Bash: `git add`, `git commit`, `git push`, `git branch`
- Read: Read changed files for PR description
- Grep: Search for related code patterns

## Prompt

```
<commit-push-pr>
Commit, push, and open a PR for the current changes.

<commit-process>
1. Review all changes with git diff
2. Stage relevant files (exclude docs/ artifacts unless requested)
3. Create commit with conventional message
4. Push to remote branch
5. Open PR with comprehensive description
</commit-process>

<pr-description>
The PR description should include:
1. **Summary** - What this PR does
2. **Changes** - List of key changes
3. **New Concepts** - Any concepts that need explanation (if teach-concepts is enabled)
4. **Testing** - How to test the changes
5. **Risks** - Any risks or considerations
6. **Related** - Links to plans, issues, or previous PRs
</pr-description>

<teaching-concepts>
If teach-concepts is enabled, explain any new concepts introduced:
- What the concept is
- Why it's needed
- How it works
- Where it's used
</teaching-concepts>
</commit-push-pr>

<output-format>
PR created:
```
Branch: {branch-name}
Commit: {commit-hash}
PR: {pr-url}
Title: {pr-title}
```
</output-format>

## Examples

### Basic commit-push-pr:
```
/ce-commit-push-pr
```

### With custom PR title:
```
/ce-commit-push-pr --pr-title "Add Fibonacci matrix V3 implementation"
```

### With reviewers:
```
/ce-commit-push-pr --reviewers "@user1 @user2"
```

### Draft PR:
```
/ce-commit-push-pr --draft
```

## Notes

- Automatically detects new concepts and explains them in PR description
- Integrates with `/ce-babysit-pr` for CI monitoring
- PR descriptions are stored in docs/prs/ for reference
- Follows conventional commit format for commit messages