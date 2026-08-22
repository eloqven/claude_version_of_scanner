---
name: ce-setup
description: Diagnose tool capabilities and create repo config.yaml. Sets up the project for Compound Engineering workflow.
---

# /ce-setup

## Description

Diagnose tool capabilities and create repo `config.yaml`. Sets up the project for Compound Engineering workflow.

## When to Use

- At project setup or onboarding
- When adding CE skills to a new project
- When diagnosing capability issues
- Before starting the core loop

## Input

- **project**: Project to set up (defaults to current directory)
- **capabilities**: Specific capabilities to check

## Output

- **File**: `config.yaml` in project root
- **File**: `docs/setup/capabilities-{timestamp}.md`

## Configuration

```yaml
ce-setup:
  auto-install: false  # Whether to auto-install missing tools
  check-all: true  # Check all capabilities
  create-docs: true  # Create docs/ directory structure
```

## allowed-tools

- Bash: Check tool availability, create directories
- Read: Read existing config
- Write: Create config.yaml

## Prompt

```
<setup-process>
Diagnose project capabilities and create config.yaml.

<capability-checks>
Check for:
1. Git - version control
2. Node.js - JavaScript runtime
3. Python - Python runtime
4. Go - Go compiler
5. Rust - Rust compiler
6. Docker - Containerization
7. Testing frameworks - Jest, Pytest, etc.
8. Linters - ESLint, Prettier, etc.
9. Build tools - Make, Bazel, etc.
10. CI/CD - GitHub Actions, etc.
</capability-checks>

<config-creation>
Create config.yaml with:
- Project metadata
- Available capabilities
- Tool versions
- CE workflow settings
- Artifact paths
</config-creation>

<directory-structure>
Create docs/ directory structure:
- docs/brainstorms/
- docs/plans/
- docs/solutions/
- docs/povs/
- docs/explanations/
- docs/proofs/
- docs/polish/
- docs/promotions/
- docs/tests/
- docs/commits/
- docs/prs/
- docs/debugging/
- docs/ideas/
- docs/pulse-reports/
- docs/residual-review-findings/
</directory-structure>
</setup-process>

<output-format>
Setup report:
```
docs/setup/capabilities-{timestamp}.md
├── project: <project name>
├── capabilities:
│   ├── {tool}: {version|not-found}
│   └── ...
├── config-created: <path to config.yaml>
├── directories-created: <list of dirs>
└── recommendations: <setup recommendations>
```
</output-format>

## Examples

### Basic setup:
```
/ce-setup
```

### Setup with auto-install:
```
/ce-setup --auto-install
```

### Check specific capabilities:
```
/ce-setup --check-all false --capabilities git,node,python
```

## Notes

- Creates config.yaml with project capabilities
- Sets up docs/ directory structure for CE artifacts
- Run once per project, re-run when capabilities change
- config.yaml is tracked in repo for team consistency