# Compound Engineering Skills for opencode

This directory contains native opencode implementations of the [Compound Engineering](https://github.com/EveryInc/compound-engineering-plugin) skills. These skills replace the need for the external CE plugin while maintaining full functionality.

## Skill Inventory

### Core Loop (6 skills - enabled by default)

| Skill | Slash Command | Purpose |
|-------|--------------|---------|
| [`/ce-brainstorm`](ce-brainstorm/SKILL.md) | `/ce-brainstorm` | Interactive Q&A with one-question-at-a-time blocking and named gap lenses (Evidence, Specificity, Counterfactual, Attachment, Durability) to produce requirements-only unified plans |
| [`/ce-plan`](ce-plan/SKILL.md) | `/ce-plan` | Enrich requirements-only plans into implementation-ready plans with U-IDs, guardrails, origin tracing (R/A/F/AE IDs), and confidence checks |
| [`/ce-work`](ce-work/SKILL.md) | `/ce-work` | Execute implementation-ready plans natively or cross-model, with durable progress tracking, transactional commits, and shipping integration |
| [`/ce-simplify-code`](ce-simplify-code/SKILL.md) | `/ce-simplify-code` | Refine freshly written code for clarity and reuse before review |
| [`/ce-code-review`](ce-code-review/SKILL.md) | `/ce-code-review` | Multi-agent review with skill-local reviewer personas (core: correctness, project-standards; conditional: testing, security, data-migration, performance) |
| [`/ce-compound`](ce-compound/SKILL.md) | `/ce-compound` | Capture learning into `docs/solutions/` with five outcomes (Keep/Update/Consolidate/Replace/Delete), refresh mode for drifting learnings |

### Additional Skills

More skills available when `skills.enabled: ["full"]` is set in opencode.jsonc:

| Category | Skills |
|----------|--------|
| Around the Loop | `/ce-strategy`, `/ce-product-pulse`, `/ce-sweep`, `/ce-compound-refresh` |
| On-Demand | `/ce-ideate`, `/ce-pov`, `/ce-explain`, `/ce-debug`, `/ce-prototype` |
| Research | `/ce-riffrec-feedback-analysis` |
| Git Workflow | `/ce-commit`, `/ce-commit-push-pr`, `/ce-babysit-pr`, `/ce-worktree` |
| Autonomous | `/lfg` |
| Frontend/Design | `/ce-polish`, `/ce-promote` |
| Collaboration | `/ce-proof`, `/ce-test-browser`, `/ce-test-xcode` |
| Setup | `/ce-setup`, `/ce-handoff` |

## Configuration

Add to `opencode.jsonc`:

```json
{
  "skills": {
    "docs_root": "docs",           // project-relative root for CE artifacts
    "enabled": ["core"]            // or ["full"] for all 33 skills, or selective list
  }
}
```

### `docs_root`

Relocates all CE artifact folders under one repo-relative root. Default: `docs`. Artifacts are stored under:
- `docs/brainstorms/`
- `docs/plans/`
- `docs/solutions/`
- `docs/pulse-reports/`
- `docs/residual-review-findings/`

### `enabled`

- `["core"]` - enables the 6 core loop skills (default)
- `["full"]` - enables all 33 skills
- `["ce-brainstorm", "ce-plan", ...]` - selectively enable specific skills

## Migration from CE Plugin

### Phase 1 - Core Loop
1. Add `{"plugin": ["compound-engineering@git+https://github.com/EveryInc/compound-engineering-plugin.git"]}` to opencode.json
2. Core skills become available via `/ce-brainstorm`, `/ce-plan`, etc.
3. Team uses core loop for all new work
4. Capture learnings via `/ce-compound`

### Phase 2 - Full Skill Set
1. Change `skills.enabled` to `["full"]` or selectively enable skills
2. Adopt on-demand skills as needed
3. Gradually remove CE plugin dependency

### Phase 3 - Plugin Removal
1. Remove CE plugin from opencode.json
2. Skills remain available from local `skills/` directory
3. Config.yaml artifacts remain tracked in repo

### Phase 4 - Artifact Management
1. `docs/` structure tracked in repo (or relocated via `docs_root`)
2. `/ce-compound` captures solutions that compound over time
3. `/ce-compound-refresh` addresses drifting learnings
4. Previous CE learnings read as grounding by new brainstorm/planning cycles

## Getting Started

1. Ensure `skills.enabled: ["core"]` in opencode.jsonc
2. Run `/ce-setup` to diagnose capabilities and create repo config.yaml
3. Start the core loop: `/ce-brainstorm` → `/ce-plan` → `/ce-work` → `/ce-code-review` → `/ce-compound`
4. Read existing solutions with `/ce-compound` before starting new features