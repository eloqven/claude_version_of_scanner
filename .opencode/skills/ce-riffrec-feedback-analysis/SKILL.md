---
name: ce-riffrec-feedback-analysis
description: Convert Riffrec recordings or notes into structured feedback. Extracts insights from user research sessions.
---

# /ce-riffrec-feedback-analysis

## Description

Convert Riffrec recordings or notes into structured feedback. Extracts insights from user research sessions.

## When to Use

- After user research sessions
- When analyzing Riffrec recordings
- When converting qualitative feedback to actionable items
- Before `/ce-brainstorm` to ground ideas in user feedback

## Input

- **source**: Riffrec recording, notes, or transcript
- **participants**: List of participants (if known)
- **session-type**: Type of session (interview, usability test, etc.)

## Output

- **File**: `docs/feedback/riffrec-{timestamp}.md`
- **Insights**: Structured feedback and themes

## Configuration

```yaml
ce-riffrec-feedback-analysis:
  extract-themes: true  # Auto-extract themes
  sentiment-analysis: true  # Analyze sentiment
  action-items: true  # Generate action items
```

## allowed-tools

- Read: Read transcripts or notes
- Write: Create structured feedback document
- Grep: Search for patterns in feedback

## Prompt

```
<riffrec-analysis>
Convert Riffrec recordings or notes into structured feedback.

<analysis-process>
1. Read the source material (transcript, notes, recording)
2. Extract key quotes and observations
3. Identify themes and patterns
4. Analyze sentiment
5. Generate action items
6. Structure as actionable feedback
</analysis-process>

<theme-extraction>
Extract themes using affinity mapping:
- Group similar observations
- Name each theme
- Count occurrences
- Identify severity/priority
</theme-extraction>

<sentiment-analysis>
For each observation:
- Positive: User expresses satisfaction or praise
- Neutral: User states facts without emotion
- Negative: User expresses frustration or confusion
- Intensity: Low/Medium/High
</sentiment-analysis>
</riffrec-analysis>

<output-format>
Riffrec feedback analysis:
```
docs/feedback/riffrec-{timestamp}.md
├── source: <recording/notes/transcript>
├── session-type: <interview|usability-test|etc>
├── participants: <list of participants>
├── themes:
│   ├── {theme}:
│   │   ├── description: <what this theme is about>
│   │   ├── quotes: <key quotes>
│   │   ├── count: <number of occurrences>
│   │   └── priority: <high|medium|low>
│   └── ...
├── sentiment-summary:
│   ├── positive: <count>
│   ├── neutral: <count>
│   └── negative: <count>
├── action-items:
│   ├── {item}: <description> - <priority>
│   └── ...
└── recommendations: <next steps>
```
</output-format>

## Examples

### Basic Riffrec analysis:
```
/ce-riffrec-feedback-analysis "session-transcript.txt"
```

### With sentiment analysis:
```
/ce-riffrec-feedback-analysis --sentiment-analysis "user-notes.md"
```

## Notes

- Converts qualitative feedback to structured insights
- Integrates with /ce-brainstorm for idea grounding
- Stored in docs/feedback/ for reference
- Helps prioritize user needs in planning