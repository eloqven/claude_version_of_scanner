---
name: ce-product-pulse
description: Generate time-windowed reports on user experience, product health, and key metrics. Provides regular check-ins on product state.
---

# /ce-product-pulse

## Description

Generate time-windowed reports on user experience, product health, and key metrics. Provides regular check-ins on product state.

## When to Use

- Weekly or monthly for regular check-ins
- After major releases
- When tracking product health metrics
- Before `/ce-strategy` updates

## Input

- **window**: Time window for the report (week, month, quarter)
- **metrics**: Specific metrics to include
- **audience**: Who the report is for

## Output

- **File**: `docs/pulse-reports/pulse-{window}-{timestamp}.md`
- **Report**: Time-windowed product health report

## Configuration

```yaml
ce-product-pulse:
  window: "week"  # week | month | quarter | custom
  metrics: "all"  # all | custom | specific
  include-user-feedback: true  # Include user feedback
  include-metrics: true  # Include quantitative metrics
```

## allowed-tools

- Bash: Run analytics queries, git log
- Read: Read metrics dashboards, user feedback
- Grep: Search for relevant data
- Glob: Find metric files

## Prompt

```
<product-pulse>
Generate a time-windowed report on product health and user experience.

<report-sections>
1. **Executive Summary** - High-level health status
2. **User Experience** - User feedback, support tickets, NPS
3. **Product Metrics** - Key metrics and trends
4. **Engineering Health** - Code quality, test coverage, incidents
5. **Market Position** - Competitive landscape, market feedback
6. **Risks and Opportunities** - Key risks and opportunities
7. **Action Items** - Next steps
</report-sections>

<metric-categories>
- User engagement: DAU, WAU, MAU, retention
- Performance: Load time, error rate, uptime
- Business: Revenue, conversion, churn
- Quality: Bug count, test coverage, code health
</metric-categories>
</product-pulse>

<output-format>
Product pulse report:
```
docs/pulse-reports/pulse-{window}-{timestamp}.md
├── window: <time period covered>
├── executive-summary:
│   ├── health: <green|yellow|red>
│   ├── key-metric: <primary metric>
│   └── trend: <up|down|stable>
├── user-experience:
│   ├── feedback: <summary of user feedback>
│   ├── support-tickets: <count and themes>
│   └── nps: <score if available>
├── product-metrics:
│   ├── {metric}: <value> ({trend})
│   └── ...
├── engineering-health:
│   ├── test-coverage: <percentage>
│   ├── bug-count: <count>
│   ├── incidents: <count>
│   └── code-quality: <score>
├── market-position:
│   ├── competitive-landscape: <summary>
│   └── market-feedback: <summary>
├── risks-and-opportunities:
│   ├── risks: <list>
│   └── opportunities: <list>
└── action-items:
    ├── {item}: <owner> - <due-date>
    └── ...
```
</output-format>

## Examples

### Weekly pulse:
```
/ce-product-pulse --window week
```

### Monthly pulse with custom metrics:
```
/ce-product-pulse --window month --metrics "dau,conversion,error-rate"
```

### Quarterly pulse:
```
/ce-product-pulse --window quarter
```

## Notes

- Run regularly (weekly/monthly) for consistent monitoring
- Integrates with /ce-strategy for strategic alignment
- Stored in docs/pulse-reports/ for historical tracking
- Helps identify trends and issues early