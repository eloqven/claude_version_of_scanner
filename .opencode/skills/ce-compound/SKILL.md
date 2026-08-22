---
name: ce-compound
description: Capture the learning into docs/solutions/ so the next loop starts smarter. Documents solved problems with five outcomes: Keep/Update/Consolidate/Replace/Delete.
---

# /ce-compound

## Description

Capture the learning into `docs/solutions/` so the next loop starts smarter. Documents solved problems with five outcomes: Keep/Update/Consolidate/Replace/Delete.

## When to Use

- After `/ce-code-review` when work is complete and PR is approved
- After any significant problem resolution where learnings should be preserved
- At the end of every `/ce-work` cycle to compound team knowledge
- When starting a new feature that relates to previously solved problems

## Prompt

```
<five-outcomes>
Each solved problem becomes a solution entry with exactly one of these outcomes:

1. **Keep** - Still relevant, no changes needed. Add to grounding for future brainstorms/plans.
2. **Update** - Partially relevant, needs updating for current context. Update the existing entry and note what changed.
3. **Consolidate** - Multiple similar solutions exist. Merge into one stronger solution, delete duplicates.
4. **Replace** - A better approach now exists. Document the new approach and mark the old one as replaced.
5. **Delete** - No longer relevant, removed from active knowledge. Archive with reason why.

<solution-format>
Solution entries live in `docs/solutions/` and follow this structure:

```
solution-{timestamp}.md
├── title: <descriptive title of the problem solved>
├── problem-statement: <what was the issue/context>
├── solution-approach: <what was done, key decisions>
├── outcome: "Keep/Update/Consolidate/Replace/Delete"
├── related-u-ids: ["<ce-plan U-IDs this relates to>"]
├── lessons-learned: <3-5 bullet points for future reference>
├── anti-patterns: <what NOT to do, common pitfalls avoided>
├── confidence: 1-5 rating for how well this solution generalizes
├── last-reviewed: <timestamp>
└── tags: ["<relevant-tags - e.g., authentication, performance, migration>"]
</solution-format>

<confidence-check-and-auto-deepening>
Before finalizing, rate confidence that this solution will be useful 6 months from now (1-5):
- If confidence 1-2: auto-deepen by running sub-agents to strengthen weak sections
- If confidence 3-4: note gaps and add conditional dependencies
- If confidence 5: proceed - solution is well-grounded and generalizable

<origin-tracing>
Trace all R/A/F/AE IDs forward from the original /ce-brainstorm:
- Which requirements from the original brainstorm does this solution address?
- Are there any R/A/F/AE IDs that this solution makes obsolete?
- How does this solution compound with previous entries in docs/solutions/?
</origin-tracing>

<session-settled-decisions>
Capture decisions as (session-settled: user-directed) annotations that survive across sessions:
- Decisions made during this session that should carry forward
- Annotations format: `(session-settled: user-directed: <decision description>)`
- These are read as grounding by the next /ce-brainstorm and /ce-plan cycle
</session-settled-decisions>
</five-outcomes>

<output-format>
Solution document added to `docs/solutions/`:
```
docs/solutions/solution-{timestamp}.md
```
The /ce-compound skill will also auto-update `docs/solutions/REPORT.md` with a summary of all outcomes since project start.
</output-format>