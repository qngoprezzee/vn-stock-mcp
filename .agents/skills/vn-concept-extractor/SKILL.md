# Skill: vn-concept-extractor

**Role:** Editor who turns raw extracted passages into a usable concept wiki page — synthesized definition, evolution across time, verbatim quotes, and a bridge to VN equity research.

---

## Trigger Conditions

Use this skill when the user:
- Runs `/extract-concept <concept-slug>` (e.g. `/extract-concept intrinsic-value`)
- References a `knowledge/wiki/<author>-concepts/_pending_<slug>.md` file
- Asks to synthesize a concept page from extracted passages

**Anti-patterns:**
- Don't paraphrase the verbatim quotes — preserve them exactly
- Don't add quotes that aren't in the pending file (no hallucination)
- Don't write more than 1 paragraph each for Definition and Evolution

---

## Critical Rules

- **RULE 1: Read the pending file first.** All raw passages are in `_pending_<slug>.md`. Use only those. Don't invent quotes.
- **RULE 2: Preserve verbatim quotes exactly.** Don't fix typos, modernize language, or compress wording. The reader's trust depends on it.
- **RULE 3: Cite year + source ID for every quote.** Format: `> "[verbatim]"` followed by `— Source: <year>, `<source_id>`.
- **RULE 4: The "Applied to VN" bridge is mandatory.** A concept page without practical application is just a quote dump. Always end with 1-2 sentences on how this concept applies to Vietnamese equity research specifically (banking room caps, parent-vs-consolidated reporting, sector cyclicality, etc.).
- **RULE 5: Cross-link to related concepts and live tools.** Use Obsidian wikilinks: `[[buffett-concepts/owner-earnings]]`, `[[wiki/dcf]]`, link to web UI screens where appropriate.

---

## Workflow

### Step 1 — Locate and parse the pending file
Read `knowledge/wiki/<author>-concepts/_pending_<slug>.md`. Confirm:
- Concept name (from frontmatter)
- Author (from frontmatter)
- Number of passages (target: 4-8 for a good page)

If only 1-2 passages, tell the user the result may be thin and ask if they want to add synonyms.

### Step 2 — Group passages by year/chronology
Sort the passages chronologically. Note whether the concept appears repeatedly or only once.

### Step 3 — Write the synthesis
Use exactly this structure:

```markdown
---
concept: <concept_name>
synthesized_by: claude-code
author: <author>
source_ids: [<id1>, <id2>, ...]
years_covered: [<year>, <year>, ...]
related_concepts: [<slug>, <slug>]
related_tools: [<tool_url_or_route>]
---

# <Concept Title> — <Author>'s View

## Definition

[Exactly 1 paragraph synthesized from the passages. Define the concept in
<Author>'s own framework, using language consistent with their quotes.
3-5 sentences.]

## Evolution

[Exactly 1 paragraph noting if <Author>'s thinking on this concept evolved
across the time range covered. If it didn't (concept is stable), say so
explicitly: "The concept is consistent across all <N> letters/memos surveyed."]

## Selected passages

### <Year 1>: <Title>
> "[verbatim quote, no edits]"

*Source: `<source_id>`*

### <Year 2>: <Title>
> "[verbatim quote]"

*Source: `<source_id>`*

[... 4-6 passages total]

## Applied to VN equity research

[Exactly 1-2 sentences. Translate the universal principle to something
Vietnamese-specific. Examples:
- "For VN banks, intrinsic value tracks book value × ROE/cost-of-equity ratio
  more cleanly than P/E, because reported earnings are heavily affected by
  IFRS-9 provisioning timing."
- "For VN real estate developers, owner earnings differ sharply from GAAP
  net profit due to land bank revaluation timing — adjust accordingly."]

## See also

- [[<author>-concepts/<related-slug-1>]]
- [[wiki/<related-concept-from-our-wiki>]]
- Live tool: [<tool name>](http://localhost:3000/<route>) — apply this concept
```

### Step 4 — Save and report
Write to `knowledge/wiki/<author>-concepts/<concept-slug>.md`. Delete the `_pending_<slug>.md` file (or leave it as audit trail if the user prefers; default = delete).

Tell the user:
> "Concept page saved to `knowledge/wiki/<author>-concepts/<slug>.md` with <N> verbatim quotes spanning <year-range>. Linked concepts: <list>."

---

## Tone & Length

- **Length**: ~400-600 words total (Definition ~80, Evolution ~80, Passages dominate, Applied ~40, See also ~20)
- **Voice**: Editor's voice for Definition + Evolution + Applied. Verbatim for passages.
- **No editorial in the quotes themselves**: The reader sees Buffett's exact words; you only frame them.

---

## Example output (abridged, for "intrinsic value" / Buffett)

```markdown
# Intrinsic Value — Warren Buffett's View

## Definition

Buffett defines intrinsic value as the discounted value of cash that can be
taken out of a business during its remaining life. He distinguishes this
sharply from book value (an accounting figure tied to past inputs) and from
market price (which reflects sentiment as much as fundamentals). Two analysts
working from the same data will produce different intrinsic value estimates
— this irreducible subjectivity is a feature, not a bug.

## Evolution

The concept is remarkably stable across the 5 letters surveyed (2019-2023).
The 2022 letter adds nuance on share repurchases below intrinsic value as a
form of intrinsic-value-per-share growth, but the underlying definition is
unchanged from earlier years.

## Selected passages

### 2019: Berkshire's Performance vs. the S&P 500
> "Intrinsic value can be defined simply: It is the discounted value of the
> cash that can be taken out of a business during its remaining life..."

*Source: `berkshire-hathaway-letters_2026-06-04_525aa6d3`*

### 2022: ... [continues with 3-5 more passages]

## Applied to VN equity research

For VN tech-services companies like FPT, intrinsic value is best estimated
via DCF on Free Cash Flow rather than P/E multiples — reported earnings are
distorted by capitalized R&D and ESOP issuance.

## See also

- [[buffett-concepts/owner-earnings]]
- [[wiki/dcf]]
- Live tool: [DCF Valuation](http://localhost:3000/screener) — apply this concept to a VN ticker
```

---

## Output Quality Checklist

- [ ] All quotes in the output were also in the pending file (no hallucination)
- [ ] Every quote has a year + source ID
- [ ] Definition and Evolution each fit in one paragraph
- [ ] "Applied to VN equity research" has at least one Vietnam-specific example
- [ ] At least 2 cross-links (related concepts or live tools)
- [ ] File saved to `knowledge/wiki/<author>-concepts/<slug>.md`
