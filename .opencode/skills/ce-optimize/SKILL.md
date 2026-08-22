---
name: ce-optimize
description: Optimize code for performance, memory, or other metrics. Uses profiling evidence to guide optimization decisions.
---

# /ce-optimize

## Description

Optimize code for performance, memory, or other metrics. Uses profiling evidence to guide optimization decisions.

## When to Use

- When performance is a concern
- After `/ce-work` to optimize hot paths
- When memory usage is too high
- Before `/ce-code-review` for performance-critical changes

## Input

- **target**: Code to optimize (function, module, service)
- **metric**: What to optimize (performance, memory, bundle-size)
- **baseline**: Current performance baseline

## Output

- **Code**: Optimized implementation
- **File**: `docs/optimizations/optimize-{timestamp}.md`

## Configuration

```yaml
ce-optimize:
  metric: "performance"  # performance | memory | bundle-size | cpu
  profiling-tool: "auto"  # auto | perf | py-spy | chrome-devtools
  target-improvement: "20%"  # Target improvement percentage
```

## allowed-tools

- Bash: Run profiling tools, benchmarks
- Read: Read code and profiling output
- Write: Modify code
- Grep: Search for patterns

## Prompt

```
<optimization-process>
Optimize {{target}} for {{metric}}.

<profiling>
1. Profile current implementation
2. Identify bottlenecks with evidence
3. Form hypotheses about optimization opportunities
4. Implement optimizations
5. Measure improvement
6. Verify no regressions
</profiling>

<optimization-principles>
- Measure first, optimize second
- Focus on biggest bottlenecks
- Prefer algorithmic improvements over micro-optimizations
- Document before/after metrics
- Ensure correctness is not compromised
</optimization-principles>
</optimization-process>

<output-format>
Optimization report:
```
docs/optimizations/optimize-{timestamp}.md
├── target: <code being optimized>
├── metric: <what was optimized>
├── baseline: <before metrics>
├── bottlenecks:
│   ├── {location}: <description> - <evidence>
│   └── ...
├── optimizations:
│   ├── {change}: <description> - <expected impact>
│   └── ...
├── results:
│   ├── before: <metrics>
│   ├── after: <metrics>
│   └── improvement: <percentage>
└── recommendations: <further optimization opportunities>
```
</output-format>

## Examples

### Basic optimization:
```
/ce-optimize "user search function" --metric performance
```

### Memory optimization:
```
/ce-optimize --metric memory "data processing pipeline"
```

### Bundle size optimization:
```
/ce-optimize --metric bundle-size "frontend bundle"
```

## Notes

- Always profile before optimizing
- Focus on evidence-based optimizations
- Document before/after metrics
- Integrates with /ce-code-review for performance review