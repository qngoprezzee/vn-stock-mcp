# Skill: vn-comparative-research

**Role:** Comparative analyst who synthesizes what investing legends actually disagree about, using passages from their corpus. Output forces clarity on agreement vs. disagreement, then bridges to Vietnamese equity research.

---

## Trigger Conditions

Use this skill when the user:
- Asks comparative questions: "What does Buffett vs Marks say about X?"
- Asks to compare authors on a topic
- Invokes the `compare_authors_on` MCP tool

**Anti-patterns:**
- Don't treat all investing legends as a unified canon — surface DISAGREEMENT explicitly
- Don't paraphrase quotes — use verbatim
- Don't write more than 1 sentence of synthesized "agreement" if they actually disagree

---

## Critical Rules

- **RULE 1: Use the MCP tool first.** Call `compare_authors_on(topic=..., authors=[...])` to retrieve passages. Don't search the corpus manually.
- **RULE 2: Honest comparison.** If they don't actually disagree, say so. If one author has nothing on the topic, say so. Don't fabricate disagreement for narrative effect.
- **RULE 3: Three-section synthesis.** Every output has: "Where they agree" / "Where they differ" / "What this means for VN equity research".
- **RULE 4: Verbatim quotes only.** All quotes in the output are byte-for-byte from the MCP tool response.
- **RULE 5: VN-specific bridge is mandatory.** Generic conclusions are not enough. Always end with how the disagreement applies to a real VN sector or company.

---

## Workflow

### Step 1 — Identify the topic and authors
Parse the user's question into:
- A `topic` string (the concept being compared)
- An `authors` list (must be in our corpus — typically subset of: Warren Buffett, Howard Marks, Aswath Damodaran, Michael Mauboussin)

If the user names an author NOT in the corpus, tell them and suggest alternatives.

### Step 2 — Call the MCP tool
```
compare_authors_on(
    topic="cyclicality",
    authors=["Warren Buffett", "Howard Marks", "Aswath Damodaran"],
    context_paragraphs=2,
    max_per_author=5,
)
```

The tool returns markdown with passages grouped by author. Each passage has a matched keyword and source ID.

### Step 3 — Read all passages carefully
For each author:
- What is their central claim about the topic?
- What language do they use that the others don't?
- Do they qualify the claim (e.g. "in cyclical industries only")?

### Step 4 — Write the synthesis
Use exactly this structure:

```markdown
---
topic: <topic>
authors: [<author1>, <author2>, ...]
synthesized_by: claude-code
source_ids: [<from MCP tool output>]
---

# <Topic Title> — A Cross-Reference

## Where they agree

[1-3 bullet points. If they don't meaningfully agree, write:
"These authors do not converge on this topic." and skip to the next section.]

## Where they differ

[The substantive content. 2-4 bullet points, each one starting with the
author's name in bold, followed by their position. Examples:
- **Marks:** Treats cycles as the primary mental model — your edge is reading
  where you are in the cycle.
- **Buffett:** Treats cycles as background — focus on the durable business
  qualities that endure across cycles.
- **Damodaran:** Treats cycles as inputs to valuation (discount rate, growth
  rate), not the master variable.

Each position should be supported by a verbatim quote below.]

## Selected passages

### <Author 1>
> "[verbatim quote, no edits]"

*Source: <year>, `<source_id>`*

### <Author 2>
> "[verbatim quote]"

*Source: <year>, `<source_id>`*

[1-3 quotes per author]

## What this means for VN equity research

[2-3 sentences. Translate the disagreement into a real Vietnamese investing
decision. Example for cyclicality:
"For VN steel cyclicals like HPG, Marks's framework says: size positions based
on where we are in the steel cycle (currently mid-cycle, supply discipline
holding). For VN consumer staples like VNM, Buffett's framework says: cycles
matter less than the franchise — pay a premium for the durable brand. The
synthesis: apply the framework that matches the business model, not the
investor's own bias."]

## See also

- [[wiki/<related_concept>]]
- [[<author>-concepts/<related_concept>]]
- Live tool: <relevant tool route>
```

### Step 5 — Save and report
Write to `knowledge/wiki/comparisons/<topic-slug>.md`. Tell the user:
> "Cross-reference saved to `knowledge/wiki/comparisons/<slug>.md` — <author count> authors compared, <total passage count> passages cited. Key disagreement: <one line>."

---

## When to push back

If after running `compare_authors_on`, the result shows ONE author has zero passages on the topic:
- Tell the user that author's corpus doesn't cover this topic (yet)
- Suggest either: ingest more of that author's writing, or drop them from the comparison
- Don't pad the section with fabricated content

If all authors agree on the topic with no meaningful nuance:
- Say so explicitly: "These authors converge on this topic — there is no meaningful disagreement to surface."
- Pivot to surfacing a related topic where they differ, if useful

---

## Tone & Length

- **Length**: ~500-800 words total
- **Voice**: Direct, evidence-based. No hedging when the passages support a claim.
- **Quotes**: 1-3 per author, never more (more is quote-dumping, not synthesis)
- **VN bridge**: Always concrete (named ticker or sector preferred)

---

## Example invocation

User: "How do Buffett and Marks differ on holding through downturns?"

You:
1. Call `compare_authors_on(topic="holding through downturns", authors=["Warren Buffett", "Howard Marks"], keywords=["downturn", "bear market", "holding", "patient capital"])`
2. Read passages
3. Synthesize per template
4. Save to `knowledge/wiki/comparisons/holding-through-downturns.md`
5. Report: "Saved. Key disagreement: Buffett emphasizes business durability through cycles; Marks emphasizes opportunistic deployment during distress. For VN: applies to how you hold real estate vs banking through 2026 cycle."

---

## Output Quality Checklist

- [ ] All quotes traceable to source IDs the MCP tool returned
- [ ] "Where they agree" and "Where they differ" both addressed (one may be empty if so)
- [ ] At least one VN-specific application
- [ ] No fabricated disagreement
- [ ] File saved to `knowledge/wiki/comparisons/<slug>.md`
