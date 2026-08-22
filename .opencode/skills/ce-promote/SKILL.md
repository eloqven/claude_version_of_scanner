---
name: ce-promote
description: Draft user-facing announcement copy for features, releases, or product updates. Creates compelling, clear, and audience-appropriate messaging.
---

# /ce-promote

## Description

Draft user-facing announcement copy for features, releases, or product updates. Creates compelling, clear, and audience-appropriate messaging.

## When to Use

- Before releasing a new feature
- When announcing product updates
- For changelog entries
- For marketing communications

## Input

- **feature**: The feature or release to announce
- **audience**: Target audience (users, developers, stakeholders)
- **channel**: Where the announcement will be published

## Output

- **File**: `docs/promotions/promo-{feature}-{timestamp}.md`
- **Content**: Announcement copy for various channels

## Configuration

```yaml
ce-promote:
  tone: "professional"  # professional | casual | excited | technical
  channels: ["changelog", "email", "social"]  # Default channels
  include-technical-details: false  # Whether to include technical details
```

## allowed-tools

- Read: Read feature documentation and code
- Grep: Search for related features and context

## Prompt

```
<promotion-creation>
Draft user-facing announcement copy for: {{feature}}

<announcement-principles>
- Clear and concise: Get to the point quickly
- User-focused: Explain what's in it for the user
- Benefit-driven: Focus on outcomes, not features
- Actionable: Tell users what to do next
- Consistent: Match brand voice and tone
</announcement-principles>

<content-structure>
1. **Headline** - Compelling, benefit-focused title
2. **Summary** - One-sentence overview
3. **What's New** - Key features and improvements
4. **Why It Matters** - User benefits and impact
5. **How to Use** - Getting started instructions
6. **Call to Action** - What to do next
</content-structure>

<channel-adaptation>
Adapt content for each channel:
- Changelog: Technical, concise, structured
- Email: Personal, benefit-focused, narrative
- Social: Brief, engaging, hashtag-friendly
- Blog: Detailed, story-driven, educational
</channel-adaptation>
</promotion-creation>

<output-format>
Promotion package:
```
docs/promotions/promo-{feature}-{timestamp}.md
├── headline: <compelling title>
├── summary: <one-sentence overview>
├── channels:
│   ├── changelog: <changelog entry>
│   ├── email: <email announcement>
│   ├── social: <social media post>
│   └── blog: <blog post draft>
├── key-points: <list of key messages>
└── call-to-action: <what users should do>
```
</output-format>

## Examples

### Basic promotion:
```
/ce-promote "new dashboard with real-time analytics"
```

### For developers:
```
/ce-promote --audience developers --tone technical "API v2 release"
```

### Multi-channel:
```
/ce-promote --channels changelog,email,social "performance improvements"
```

## Notes

- Tone and channel can be customized
- Integrates with /ce-explain for technical documentation
- Promotion copy is stored in docs/promotions/ for reference