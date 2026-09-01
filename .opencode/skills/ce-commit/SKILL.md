---
name: ce-commit
description: Create a git commit with a clear, conventional message. Follows project conventions for commit format, scope, and breaking change indicators.
---

# /ce-commit

## Description

Create a git commit with a clear, conventional message. Follows project conventions for commit format, scope, and breaking change indicators.

## When to Use

- After completing work on a U-ID or feature
- Before pushing changes
- When you need a well-formatted commit message
- Before `/ce-commit-push-pr` or `/ce-babysit-pr`

## Input

- **changes**: Description of what was changed
- **scope**: Optional scope for the commit (e.g., "auth", "ui", "api")
- **breaking**: Whether this is a breaking change

## Output

- **Git commit**: Created commit with conventional message
- **File**: `docs/commits/commit-{timestamp}.md` (optional log)

## Configuration

```yaml
ce-commit:
  format: "conventional"  # conventional | freeform | emoji
  require-scope: false  # Whether scope is required
  sign-commits: false  # Whether to GPG sign commits
  auto-stage: false  # Whether to auto-stage changes
```

## allowed-tools

- Bash: `git add`, `git commit`, `git status`, `git diff`
- Read: Read changed files for context

## Prompt

```
<commit-creation>
Create a git commit with a clear, conventional message for the current changes.

<commit-message-format>
Use conventional commit format:
{type}({scope}): {subject}

{body}

{footer}

Types: feat | fix | docs | style | refactor | perf | test | build | ci | chore | revert

Breaking changes: Add ! after type or BREAKING CHANGE: in footer
</commit-message-format>

<commit-process>
1. Review staged changes with git diff --staged
2. If nothing is staged, stage relevant files
3. Generate commit message based on changes
4. Include breaking change indicators if applicable
5. Create the commit
6. Verify the commit was created successfully
</commit-process>
</commit-creation>

<output-format>
Commit created:
```
commit {hash}
Author: {author}
Date:   {date}

    {type}({scope}): {subject}
    
    {body}
    
    {footer}
```
</output-format>

## Examples

### Basic commit:
```
/ce-commit
```

### Commit with scope:
```
/ce-commit --scope auth
```

### Breaking change commit:
```
/ce-commit --breaking --scope api
```

### Commit with custom message:
```
/ce-commit --message "feat(auth): add OAuth2 support"
```

## Notes

- Follows conventional commit specification
- Auto-detects breaking changes from diff patterns
- Can be used standalone or as part of `/ce-commit-push-pr`
- Commit messages are stored in docs/commits/ for reference