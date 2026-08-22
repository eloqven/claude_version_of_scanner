---
name: ce-compound-refresh
description: Refresh stale or drifting learnings in docs/solutions/. Identifies solutions that may no longer be relevant and updates them.
---

# /ce-compound-refresh

## Description

Refresh stale or drifting learnings in `docs/solutions/`. Identifies solutions that may no longer be relevant and updates them.

## When to Use

- Periodically (quarterly recommended)
- When project context has changed significantly
- After major refactors or architecture changes
- When solutions seem outdated

## Input

- **scope**: Which solutions to refresh (all, stale, specific)
- **threshold**: Age threshold for "stale" (default: 90 days)

## Output

- **File**: `docs/solutions/refresh-{timestamp}.md`
- **Updates**: Refreshed solution entries

## Configuration

```yaml
ce-compound-refresh:
  threshold-days: 90  # Days before considered stale
  auto-update: false  # Whether to auto-update solutions
  confidence-threshold: 3  # Min confidence to keep without review
```

## allowed-tools

- Read: Read existing solutions
- Write: Update solution entries
- Grep: Search for related code changes
- Glob: Find solution files

## Prompt

```
<refresh-process>
Refresh stale or drifting learnings in docs/solutions/.

<refresh-criteria>
A solution is stale if:
1. Last reviewed > threshold-days ago
2. Related code has changed significantly
3. Confidence rating < confidence-threshold
4. Tags indicate deprecated technology
</refresh-criteria>

<refresh-actions>
For each solution:
1. **Keep** - Still relevant, no changes needed
2. **Update** - Partially relevant, needs updating
3. **Consolidate** - Merge with similar solutions
4. **Replace** - Better approach now exists
5. **Delete** - No longer relevant

<drift-detection>
Check for drift by:
- Comparing solution to current code
- Checking if assumptions still hold
- Verifying if technology is still used
- Confirming if problem still exists
</drift-detection>
</refresh-process>

<output-format>
Refresh report:
```
docs/solutions/refresh-{timestamp}.md
├── scope: <all|stale|specific>
├── solutions-reviewed: <count>
├── outcomes:
│   ├── {solution}:
│   │   ├── outcome: <Keep|Update|Consolidate|Replace|Delete>
│   │   ├── reason: <why this outcome>
│   │   ├── changes: <what was changed>
│   │   └── confidence: <new confidence rating>
│   └── ...
├── summary:
│   ├── kept: <count>
│   ├── updated: <count>
│   ├── consolidated: <count>
│   ├── replaced: <count>
│   └── deleted: <count>
└── recommendations: <further actions>
```
</output-format>

## Examples

### Refresh all solutions:
```
/ce-compound-refresh --scope all
```

### Refresh stale solutions:
```
/ce-compound-refresh --scope stale
```

### Auto-update with low threshold:
```
/ce-compound-refresh --threshold-days 30 --auto-update
```

## Notes

- Run quarterly to maintain solution quality
- Integrates with /ce-compound for new learnings
- Stored in docs/solutions/ alongside solutions
- Helps prevent knowledge decay over time