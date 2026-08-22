---
name: ce-proof
description: Create, edit, and share Proof documents. Proof documents are structured evidence that something works, is correct, or meets requirements.
---

# /ce-proof

## Description

Create, edit, and share Proof documents. Proof documents are structured evidence that something works, is correct, or meets requirements.

## When to Use

- When you need to prove correctness of an implementation
- For documenting test results and evidence
- When sharing verification with stakeholders
- Before `/ce-compound` to capture verified learnings

## Input

- **subject**: What to prove or verify
- **evidence**: Evidence to include (test results, logs, screenshots)
- **format**: Proof document format

## Output

- **File**: `docs/proofs/proof-{subject}-{timestamp}.md`
- **Content**: Structured proof document with evidence

## Configuration

```yaml
ce-proof:
  format: "standard"  # standard | minimal | detailed
  include-screenshots: false  # Whether to capture screenshots
  auto-verify: true  # Whether to run verification automatically
```

## allowed-tools

- Read: Read test files and results
- Bash: Run tests, capture output
- Glob: Find test files

## Prompt

```
<proof-creation>
Create a Proof document for: {{subject}}

<proof-structure>
1. **Claim** - What is being proven
2. **Method** - How it was verified
3. **Evidence** - Test results, logs, screenshots
4. **Analysis** - What the evidence shows
5. **Conclusion** - Whether the claim is proven
6. **Limitations** - What wasn't tested
</proof-structure>

<evidence-collection>
Collect evidence through:
- Automated tests (unit, integration, e2e)
- Manual verification steps
- Log analysis
- Performance benchmarks
- Security scans
</evidence-collection>
</proof-creation>

<output-format>
Proof document:
```
docs/proofs/proof-{subject}-{timestamp}.md
├── claim: <what is being proven>
├── method: <how it was verified>
├── evidence:
│   ├── test-results: <test output>
│   ├── logs: <relevant log excerpts>
│   ├── screenshots: <if applicable>
│   └── benchmarks: <performance data>
├── analysis: <what evidence shows>
├── conclusion: <proven|not-proven|partially-proven>
└── limitations: <what wasn't tested>
```
</output-format>

## Examples

### Basic proof:
```
/ce-proof "authentication flow handles edge cases"
```

### With screenshots:
```
/ce-proof --include-screenshots "checkout page renders correctly on mobile"
```

### Detailed proof:
```
/ce-proof --format detailed "API response time under 100ms"
```

## Notes

- Proof documents provide structured evidence
- Integrates with /ce-compound for learning capture
- Stored in docs/proofs/ for reference
- Can be shared with stakeholders for verification