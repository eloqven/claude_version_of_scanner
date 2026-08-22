---
name: ce-worktree
description: Ensure work happens in an isolated git worktree. Creates, manages, and cleans up worktrees for feature development.
---

# /ce-worktree

## Description

Ensure work happens in an isolated git worktree. Creates, manages, and cleans up worktrees for feature development.

## When to Use

- Before starting new feature work
- When you need isolation from main branch
- Before `/ce-work` to ensure clean environment
- When working on multiple features simultaneously

## Input

- **branch**: Branch name for the worktree
- **base**: Base branch to create worktree from (default: main)
- **cleanup**: Whether to clean up worktree after use

## Output

- **Git worktree**: Created at `worktrees/{branch-name}`
- **File**: `docs/worktrees/worktree-{timestamp}.md`

## Configuration

```yaml
ce-worktree:
  worktree-root: "worktrees"  # Root directory for worktrees
  auto-cleanup: false  # Clean up worktree when done
  base-branch: "main"  # Default base branch
```

## allowed-tools

- Bash: `git worktree add`, `git worktree remove`, `git branch`
- Read: Read worktree status

## Prompt

```
<worktree-management>
Create and manage an isolated git worktree for feature development.

<worktree-process>
1. Check if branch already exists
2. Create worktree at worktrees/{branch-name}
3. Switch to the worktree
4. Verify clean state
5. Report worktree status
</worktree-process>

<cleanup>
When auto-cleanup is enabled:
1. Verify all changes are committed or stashed
2. Remove the worktree
3. Delete the branch if merged
4. Report cleanup status
</cleanup>
</worktree-management>

<output-format>
Worktree created:
```
Branch: {branch-name}
Path: worktrees/{branch-name}
Base: {base-branch}
Status: {clean|dirty}
```
</output-format>

## Examples

### Create worktree for feature:
```
/ce-worktree --branch feature/new-auth
```

### Create from specific base:
```
/ce-worktree --branch feature/new-auth --base develop
```

### Cleanup worktree:
```
/ce-worktree --cleanup --branch feature/new-auth
```

## Notes

- Worktrees provide isolation from main branch
- Automatically creates branch if it doesn't exist
- Integrates with `/ce-work` for isolated development
- Worktrees are stored in worktrees/ directory (gitignored)