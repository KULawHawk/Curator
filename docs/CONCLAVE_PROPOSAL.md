# Conclave — Multi-Lens Ensemble Indexer for Assessment Knowledge Bases

**Status:** v0.1 design proposal (forward-looking; not yet scheduled for build)
**Date:** 2026-05-08
**Authored for:** Jake Leese, in response to the request for a "most advanced indexer ever" featuring 5-9 distinct indexers that vote on output quality
**Companion documents:**
- `docs/APEX_INFO_RESPONSE.md` — APEX architecture inventory (relevant to integration questions)
- `ECOSYSTEM_DESIGN.md` — broader Curator + APEX + Umbrella + Nestegg ecosystem design
- This doc adds **Conclave** as a candidate fifth product in the constellation

---

## TL;DR

**Conclave** is an ensemble indexing system: 5-9 independent extractors (each called a **Lens**) ingest the same source document via different methods (text extraction, OCR, layout-aware ML, vision-language models, table specialists, etc.), produce candidate Knowledge Base outputs, then collectively vote on the best version section-by-section. Disagreements are flagged with each side's argument; close calls (1-vote margins) are explicitly logged for review; full audit trail of every vote is preserved.

The mathematical case: individual indexers cap out at ~90-95% accuracy on complex psychological assessment manuals (tables + multi-column layouts + figures + scoring rubrics). Ensemble voting with uncorrelated errors can push effective accuracy past 99%, which matches APEX's Constitution §1 accuracy aim ("≥99.5% minimum").

The ecosystem case: Conclave fills the gap APEX's planned Succubus (subAPEX3) was designed for, but with an architecture that uses Curator as the file-inventory backbone and outputs APEX-consumable manifests. It can be either Succubus's evolution OR a standalone constellation product that Succubus consumes. Recommendation: **standalone constellation product**, named per the constellation convention, integrating bidirectionally with Curator and APEX without being either.

**Effort estimate:** Substantial. ~80-150h to MVP (5 Lenses + basic voting + APEX-consumable output). Not a sprint. Best built in phases starting after Curator hits 1.0.

---

## 1. Problem statement

APEX's Constitution §1 demands ≥99.5% accuracy on every clinical claim. Today's assessment-KB construction (via Vampire, the current indexer) relies on a single extraction approach per page. Single-method extractors have characteristic failure modes:

| Method | Strong on | Fails on |
|---|---|---|
| PDF text extraction | Born-digital text | Scanned pages; embedded figure text; rotated text |
| OCR | Scanned documents | Layout collapse; multi-column ordering; tables |
| Layout-aware ML | Complex pages | Domain vocabulary; rare layouts; tables across columns |
| Vision-language model | Figures, handwriting, diagrams | Cost per page; hallucination on dense text; latency |
| Table specialists | Structured data | Anything outside table boundaries |

A single method can't be best at everything. Vampire today picks one method and lives with its failures. Succubus (in design) plans a 3-lane architecture (programmatic / offline / online) but still picks ONE lane per page based on heuristics — so the failure mode collapses to whichever lane was chosen.

**Conclave's premise:** Run them ALL. Let them disagree. Vote on the result. The redundancy IS the accuracy mechanism.

This isn't speculation. It's how transformer self-consistency (Wang et al. 2022) achieves SOTA on math word problems — by sampling N reasoning chains and majority-voting answers. It's how Whisper's word-error-rate drops when you ensemble multiple beam searches. The technique generalizes: when individual extractors have errors that are *uncorrelated* (different methods fail on different inputs), ensembling collapses error rates multiplicatively rather than additively.

---

## 2. Architecture overview

Conclave is a pipeline with five stages:

```
┌─────────────────────────────────────────────────────────────────────┐
│  STAGE 1: SOURCE PREPARATION                                        │
│  • Curator query: "give me file_id, source_path, sha256 for X"      │
│  • Page rasterization (for vision Lenses): pdftoppm at 300 DPI      │
│  • Text-layer extraction sniff: born-digital? scanned? mixed?       │
│  • Triage: which Lenses to invoke (skip OCR if pure text-layer)     │
└─────────────────────────────────────────────────────────────────────┘
                                ↓
┌─────────────────────────────────────────────────────────────────────┐
│  STAGE 2: PARALLEL LENS EXECUTION (5-9 Lenses run independently)    │
│  • Each Lens reads the source independently                         │
│  • Each produces a structured candidate output (LensOutput)         │
│  • Each emits a confidence score per section/cell/element           │
│  • No Lens sees any other Lens's output                             │
└─────────────────────────────────────────────────────────────────────┘
                                ↓
┌─────────────────────────────────────────────────────────────────────┐
│  STAGE 3: ALIGNMENT                                                 │
│  • Section-level alignment via header text + position fingerprints  │
│  • Within-section element alignment (paragraphs, tables, lists)     │
│  • Produce an N×K matrix of "Lens × Element" candidates             │
└─────────────────────────────────────────────────────────────────────┘
                                ↓
┌─────────────────────────────────────────────────────────────────────┐
│  STAGE 4: VOTING + CONSENSUS                                        │
│  • Per-element: gather N candidates                                 │
│  • Unanimous → lock in                                              │
│  • Majority (>N/2) → take majority + log dissent                    │
│  • Plurality (no majority) → flag for human review with arguments   │
│  • 1-vote-margin always flagged in audit                            │
└─────────────────────────────────────────────────────────────────────┘
                                ↓
┌─────────────────────────────────────────────────────────────────────┐
│  STAGE 5: SYNTHESIS + EMISSION                                      │
│  • Compose the consensus output                                     │
│  • Emit in APEX KB format (per APEX_MASTER_ROADMAP_v2_4.md §5.4)    │
│  • Write Conclave audit log (every vote, every argument)            │
│  • Update Curator's lineage edges (source PDF → KB output)          │
└─────────────────────────────────────────────────────────────────────┘
```

The Curator integration is at both ends: Stage 1 queries Curator for the source file and its hash; Stage 5 writes the KB output back through Curator so lineage edges are tracked automatically. APEX consumes the KB output via its existing Vampire/Succubus channels — Conclave produces APEX-format manifests, so APEX doesn't need to know Conclave exists.

---

## 3. The Lenses (proposed roster of 9)

Each Lens is a self-contained extractor with a stable interface: takes a source file path + page range, returns a `LensOutput` (structured candidate). Lenses are independent processes — a failure in one doesn't affect others.

### Lens 1: **PdfText** — Native text-layer extraction

- **Library:** pdfplumber (https://github.com/jsvine/pdfplumber, MIT)
- **Approach:** Reads PDF text objects directly. Fast, deterministic, exact.
- **Strong on:** Born-digital PDFs (manuals published in the last 20 years).
- **Fails on:** Scanned pages (returns nothing); rotated text; embedded figure captions.
- **Cost:** ~100ms per page. Free.

### Lens 2: **OcrFlow** — Tesseract OCR with reading-order reconstruction

- **Library:** pytesseract (https://github.com/madmaze/pytesseract, GPL-2 wrapper around Tesseract Apache-2)
- **Approach:** Rasterize page → Tesseract OCR with hOCR output → reconstruct reading order from bounding boxes.
- **Strong on:** Scanned documents; rotated text; older manuals (pre-2000).
- **Fails on:** Multi-column layouts (reading order breaks); dense tables; small fonts.
- **Cost:** ~3-5s per page. Free.

### Lens 3: **OcrPaddle** — PaddleOCR with layout analysis

- **Library:** PaddleOCR (https://github.com/PaddlePaddle/PaddleOCR, Apache-2)
- **Approach:** Different OCR engine; uses layout analysis to preserve column order and table boundaries.
- **Strong on:** Multi-column scanned documents; non-English text; tables.
- **Fails on:** Very low-quality scans; mathematical notation.
- **Cost:** ~5-8s per page. Free.
- **Why both Lens 2 and Lens 3:** OCR engines fail differently. Two OCR Lenses give the ensemble a tiebreaker on scanned pages.

### Lens 4: **MarkerPdf** — High-quality PDF→Markdown specialist

- **Library:** Marker (https://github.com/VikParuchuri/marker, GPL-3 with commercial-use exception)
- **Approach:** Combines text extraction + OCR + layout analysis + ML reading-order in a single optimized pipeline. Designed specifically for academic/scientific PDFs.
- **Strong on:** Complex academic manuals; equations; figures with captions.
- **Fails on:** Heavily scanned-only docs; non-Latin scripts.
- **Cost:** ~10-15s per page (uses local ML models). Free; needs ~4GB GPU or runs slower on CPU.

### Lens 5: **TableSurgeon** — Table-specialist extraction

- **Library:** Camelot (https://github.com/camelot-dev/camelot, MIT) + table-transformer (https://github.com/microsoft/table-transformer, MIT)
- **Approach:** Camelot extracts text-layer tables; table-transformer detects+extracts tables from page images. Run both; pick higher-confidence per table.
- **Strong on:** Scoring rubric tables; norm tables; percentile lookups — exactly the high-stakes content in psychometric manuals.
- **Fails on:** Non-tabular content (returns nothing — that's fine, other Lenses cover it).
- **Cost:** ~2-4s per page (only on pages flagged as containing tables).

### Lens 6: **VisionClaude** — Claude API on rendered pages

- **Library:** anthropic Python SDK (https://github.com/anthropics/anthropic-sdk-python, MIT)
- **Approach:** Render page at 300 DPI → send image to Claude Sonnet/Opus with structured prompt → parse response.
- **Strong on:** Figures with embedded text; handwritten annotations; ambiguous layouts; "what does this page mean?" semantic understanding.
- **Fails on:** Cost (API per page); rate limits; hallucination on dense text (mitigated by grounding to image).
- **Cost:** ~$0.005-0.05 per page depending on model + size. Slow (~3-8s per page).
- **Smart use:** Only invoke on pages where other Lenses disagree significantly OR pages flagged as containing complex visual elements.

### Lens 7: **VisionLocal** — Local vision-language model

- **Library:** Qwen2-VL-7B (https://github.com/QwenLM/Qwen-VL, Apache-2) or Llama-3.2-Vision-11B
- **Approach:** Same as VisionClaude but using a local VLM. Slower per page but free + offline-capable.
- **Strong on:** Same as VisionClaude. Provides a redundancy check (if local VLM and Claude both agree, very high confidence).
- **Fails on:** Lower accuracy ceiling than frontier APIs; needs 8-16GB GPU for usable speed.
- **Cost:** Free; ~15-30s per page.
- **Why both Lens 6 and Lens 7:** Cost trade-off + APEX's Self-sufficiency aim (must work offline). VisionLocal is the offline fallback; VisionClaude is the high-accuracy paid option.

### Lens 8: **StructuredHeuristic** — Domain-specific pattern extractor

- **Library:** Pure Python with regex + custom rules
- **Approach:** Encodes known patterns from psychometric assessment literature: item lists ("1. Item text\n2. Item text"), Likert scales, scoring rubrics ("Score 0 if...", "Score 1 if..."), percentile tables, normative data tables.
- **Strong on:** When patterns match, near-100% accuracy on what it claims to extract.
- **Fails on:** Anything outside its pattern library (returns nothing for unknown patterns — that's fine).
- **Cost:** <100ms per page. Free.
- **Why this matters:** Psychological assessments have *highly stereotyped* structures. A pattern-matcher trained on 50 manuals will match the next 50 with high precision.

### Lens 9: **CitationGraph** — Reference + citation extractor

- **Library:** anystyle (https://github.com/inukshuk/anystyle, MIT) — Ruby-based, callable via subprocess; alternative: GROBID (https://github.com/kermitt2/grobid, Apache-2)
- **Approach:** Identifies the manual's bibliography; extracts citations with structured metadata; flags which citations are referenced in the body text.
- **Strong on:** Building cross-assessment lineage (what does the WAIS-IV manual cite that the WISC-V manual also cites?).
- **Fails on:** Manuals with no formal bibliography (returns nothing).
- **Cost:** ~5s per document. Free.
- **Why this matters:** APEX's lineage is currently flat. Citation graphs would let APEX's Scribe trace claim → KB → source PDF → cited paper.

### Lens count rationale

You suggested 5-9. Above is 9. The actual ensemble at runtime can be a configurable subset:
- **Cheap-and-fast preset (3 Lenses):** PdfText + OcrFlow + StructuredHeuristic — for triage / first pass
- **Balanced preset (5 Lenses):** add MarkerPdf + TableSurgeon — for routine indexing
- **Full ensemble (9 Lenses):** for high-stakes assessments where 100% accuracy matters most
- **Custom subsets:** user-configurable per assessment

---

## 4. The consensus mechanism

This is where the ensemble becomes more than the sum of its parts.

### 4.1 Alignment

Before voting, Lens outputs must be aligned so we know which candidates belong to the same "thing." Algorithm:

1. **Section-level alignment:** For each Lens, extract the section heading list with page numbers. Compute pairwise sequence alignment using SequenceMatcher (stdlib `difflib`) on heading text. Identify the canonical section list as the one that appears in ≥N/2 Lenses (majority).
2. **Within-section element alignment:** For each section, align paragraphs/tables/lists across Lenses using:
   - Position alignment (page number + relative position)
   - Content fingerprint alignment (first 50 chars + length)
   - Structural alignment (table → table, paragraph → paragraph)
3. **Output:** an N × K matrix where rows are Lenses and columns are aligned elements. Cells contain the Lens's candidate for that element (or `null` if that Lens didn't produce one).

### 4.2 Per-element voting

For each column (aligned element):

```
candidates = [lens_output[i] for i in lenses if lens_output[i] is not None]
N_voting = len(candidates)

if N_voting < 2:
    # Only one Lens produced output; can't ensemble. Take it but flag low confidence.
    consensus = candidates[0]
    confidence = "single_lens"

elif all_candidates_agree(candidates, threshold=0.95):
    # Unanimous (after fuzzy match for minor whitespace differences)
    consensus = candidates[0]
    confidence = "unanimous"

else:
    # Cluster candidates by similarity
    clusters = cluster_by_similarity(candidates, threshold=0.85)
    largest_cluster = max(clusters, key=len)
    
    if len(largest_cluster) > N_voting / 2:
        # Strict majority
        consensus = consensus_within_cluster(largest_cluster)
        confidence = "majority"
        margin = len(largest_cluster) - len(second_largest_cluster)
        if margin == 1:
            confidence = "majority_one_vote_margin"  # always flagged
    else:
        # No majority — plurality at best
        consensus = None
        confidence = "no_consensus_flag_for_review"
```

### 4.3 Argument generation for flagged items

When an item is flagged for human review, Conclave generates a structured "case file":

```json
{
  "element_id": "section_3.2.paragraph_4",
  "page": 47,
  "candidates": [
    {
      "lens": "PdfText",
      "output": "Score 1 point if the response references...",
      "confidence_self_reported": 0.95,
      "argument": "Direct text-layer extraction; no inference."
    },
    {
      "lens": "OcrFlow",
      "output": "Score 1 point if the response references...",
      "confidence_self_reported": 0.82,
      "argument": "OCR with 91% character confidence."
    },
    {
      "lens": "VisionClaude",
      "output": "Score 1 point if the response refers to...",
      "confidence_self_reported": 0.88,
      "argument": "Reading the rendered page, the verb appears to be 'refers to' not 'references' — possible italic emphasis on 'refers'."
    }
  ],
  "vote_summary": "2:1 favoring 'references'",
  "recommended_action": "User review — VisionClaude flags possible italic emphasis that text extractors lose."
}
```

This case file format is the user-facing artifact for resolving disagreements. It can be reviewed in the GUI (a future Conclave tab) or in a CSV export.

### 4.4 Audit trail

Every vote is recorded. The audit log entry shape:

```json
{
  "timestamp": "...",
  "tool": "conclave",
  "version": "0.1.0",
  "actor": "ensemble",
  "action": "vote.resolved",
  "entity_id": "section_3.2.paragraph_4",
  "details": {
    "lenses_voting": ["PdfText", "OcrFlow", "MarkerPdf", "VisionClaude", "VisionLocal"],
    "consensus": "references",
    "dissenting_lenses": ["VisionClaude (preferred 'refers to')"],
    "margin": 4,
    "confidence": "majority"
  },
  "learning_trace": "Italic emphasis was correctly detected by VisionClaude but the majority preferred the verbatim text-layer extraction. For italic preservation, user may want to enable italic-aware extraction in PdfText (pdfplumber supports font-style detection)."
}
```

The `learning_trace` field satisfies APEX Constitution §4 (Compounding Learning) — every vote produces both an artifact (the consensus value) AND a learning trace (what the disagreement teaches us).

---

## 5. Logic gates and decision trees as the organizing primitive

You've consistently come back to logic gates and decision trees. Conclave maps to this naturally.

Each Lens internally produces extraction decisions as decision-tree nodes. For example, PdfText's "is this line a section heading?" decision:

```
IF font_size > body_font_size * 1.3
  AND (is_bold OR is_italic)
  AND line_height_above > body_line_height * 1.5
  AND line_starts_at_left_margin
THEN classify = HEADING (confidence 0.92)
ELSE classify = BODY (confidence 0.85)
```

Each Lens has its own such trees. The ensemble's "is this a heading?" decision becomes:

```
heading_confidence = COMBINE(
  PdfText.heading_decision,        # AND/OR-able boolean + confidence
  OcrFlow.heading_decision,        # may disagree (OCR doesn't see font sizes)
  MarkerPdf.heading_decision,      # uses ML model trained on headings
  VisionClaude.heading_decision    # semantic: "this looks like a section header"
)
```

Where `COMBINE` is a configurable logic gate composition. Default: weighted-OR with confidence weights. Strict mode: AND across high-confidence Lenses only.

The decision tree representation is preserved through to the audit log. When Conclave classifies something as a heading, the audit entry includes the full decision tree from each voting Lens — so every classification is fully introspectable.

This is the same technique used in the logic-gate extraction workflow you've been building for your other projects: each rule becomes a binary toggle, each toggle is independently auditable, the composition is explicit.

---

## 6. GitHub assets — full inventory with licenses

Per your standing endorsement to leverage GitHub libraries:

| Library | Repo | License | Used for | Substitution risk |
|---|---|---|---|---|
| pdfplumber | jsvine/pdfplumber | MIT | Lens 1 (PdfText) | Low; pypdf is fallback |
| pytesseract | madmaze/pytesseract | GPL-2 wrapper | Lens 2 (OcrFlow) | Wraps Apache-2 Tesseract |
| PaddleOCR | PaddlePaddle/PaddleOCR | Apache-2 | Lens 3 (OcrPaddle) | EasyOCR is alternative |
| Marker | VikParuchuri/marker | GPL-3 (commercial exception) | Lens 4 (MarkerPdf) | Check license terms for use case |
| Camelot | camelot-dev/camelot | MIT | Lens 5 (TableSurgeon) | Tabula is alternative |
| table-transformer | microsoft/table-transformer | MIT | Lens 5 (TableSurgeon) | First choice |
| anthropic SDK | anthropics/anthropic-sdk-python | MIT | Lens 6 (VisionClaude) | Official |
| Qwen-VL | QwenLM/Qwen-VL | Apache-2 | Lens 7 (VisionLocal) | Llama-3.2-Vision alternative |
| anystyle | inukshuk/anystyle | MIT | Lens 9 (CitationGraph) | GROBID alternative |
| GROBID | kermitt2/grobid | Apache-2 | Lens 9 alternative | Java-based; heavier |
| Unstructured | Unstructured-IO/unstructured | Apache-2 | Cross-validation reference | Could be Lens 10 |

**License watch items:**
- Marker is GPL-3 with a commercial-use exception. Read the terms before deploying commercially.
- pytesseract wraps Tesseract; the wrapper is GPL but Tesseract itself is Apache-2. Linking dynamics matter for any redistribution.
- All others are MIT or Apache-2 — fully permissive.

**Heavy assets:** Lenses 4, 6, 7 require model downloads (1-15GB each). Conclave should bundle a `conclave models download` CLI command so first-run setup is one command, not nine separate model fetches.

---

## 7. Integration with the ecosystem

### Where Conclave fits

Conclave is best built as a **standalone constellation product**, not as a Curator subsystem or APEX subAPEX. Reasoning:

- **Too specialized for Curator core.** Curator is a file knowledge graph; an ensemble indexer with vision models and OCR is a different mission.
- **Too forward-looking for Vampire.** Vampire is the current single-method indexer; Conclave is a generation beyond.
- **Could be Succubus's evolution.** Succubus's design (3-lane architecture) is a stepping stone toward Conclave's N-lens architecture. Conclave extends the idea.
- **Should integrate with all three (Curator, APEX, Umbrella) without depending on any one.**

### Curator integration

- Conclave's Stage 1 queries Curator's MCP server: "give me the source file for this assessment, with its hash."
- Conclave's Stage 5 writes the KB output back through Curator (so lineage edges are populated automatically: source PDF → Conclave run → KB Markdown).
- Conclave's audit log uses SIP audit format (per `ECOSYSTEM_DESIGN.md` §3.2) so cross-tool audit viewers can see Conclave votes alongside Curator operations.

### APEX integration

- Conclave's Stage 5 emits APEX KB format (per `APEX_MASTER_ROADMAP_v2_4.md` §5.4: `profile.json`, `manifest.json`, `verification_report.md`, `extraction/all_extractions.jsonl`, `kb/*.md`).
- APEX consumes Conclave output through its existing Vampire/Succubus channel — no APEX-side changes needed initially.
- Conclave honors all APEX hard constraints from `ECOSYSTEM_DESIGN.md` §2: never deletes assessment artifacts (writes to fresh output paths); produces SHA256 chained audit logs; all KB output respects the citation chain (every extracted claim traces to source page).

### Umbrella integration

- Conclave runs are long-lived (minutes per assessment). Umbrella monitors Conclave's progress and surfaces the per-Lens status: "Lens 4 (MarkerPdf) at 60% on page 23 of 150."
- Failed Lenses get auto-Claude-troubleshoot via Umbrella when implemented.

### Nestegg integration

- Conclave's model downloads are large and platform-specific. Nestegg bundles the right model selection per target system.

---

## 8. Phased rollout proposal

Conclave is too large to ship in one go. Phased plan:

### Phase 1: Single-Lens proof of concept (~10h)
- Build the Lens interface contract
- Implement Lens 1 (PdfText) and Lens 2 (OcrFlow)
- Two-Lens "ensemble" with simple agreement check (no real voting yet)
- Output: APEX-format KB for one test assessment

### Phase 2: Voting infrastructure (~25h)
- Add Lens 5 (TableSurgeon) and Lens 8 (StructuredHeuristic)
- Build the alignment + voting pipeline
- Build the audit log
- 4-Lens ensemble producing voted output

### Phase 3: Vision Lens integration (~30h)
- Add Lens 6 (VisionClaude) and Lens 7 (VisionLocal)
- Add the smart-invocation logic (only invoke vision Lenses on disagreement)
- Cost-budget controls (don't burn $50 indexing one manual)

### Phase 4: ML extraction Lenses (~25h)
- Add Lens 4 (MarkerPdf), Lens 3 (OcrPaddle)
- Now at 8 Lenses
- Performance tuning

### Phase 5: Citation graph + GUI (~30h)
- Add Lens 9 (CitationGraph)
- Build a Conclave GUI tab (in Curator's main window OR a standalone Conclave window)
- User-review workflow for flagged items

**Total to v1.0: ~120h.** Best built in parallel with Curator Phase Δ work, not before.

---

## 9. Open questions for Jake

- **OQ-1.** Conclave as standalone product OR as Succubus's evolution? Recommend standalone for ecosystem reasons; Succubus-evolution is also defensible.
- **OQ-2.** Project codename — Conclave, Quorum, Polyphony, Tribunal, or other? Conclave is my preference (voting body metaphor); your call.
- **OQ-3.** Build location: `C:\Users\jmlee\Desktop\AL\Conclave\` (parallel to Curator/Apex)?
- **OQ-4.** Lens count for v1: 5 (balanced), 7 (richer), or 9 (full)?
- **OQ-5.** Vision Lens budget — set a per-assessment $ cap to prevent runaway costs?
- **OQ-6.** First test assessment for proof of concept — RCS Vol. 1 (you have it indexed already so we have ground truth) or something fresh?
- **OQ-7.** Build order: after Curator 1.0 OR interleaved with Phase Δ work?
- **OQ-8.** Should Conclave's voting threshold be tunable per-section type (stricter for scoring rubrics, looser for narrative)?

---

## 10. Ideas log (Conclave-specific)

- **[CONCLAVE-IDEA-01]** Confidence-weighted voting: each Lens self-reports per-element confidence; votes are weighted by self-confidence × historical Lens accuracy on similar content. More sophisticated than raw majority.
- **[CONCLAVE-IDEA-02]** Lens performance learning: track per-Lens accuracy over time on user-resolved disagreements; auto-tune which Lenses get higher vote weights.
- **[CONCLAVE-IDEA-03]** "Disagreement explorer" GUI: a tab that surfaces every flagged disagreement across all indexing runs, sortable by margin, by Lens involvement, by section type. Helps Jake spot systemic Lens weaknesses.
- **[CONCLAVE-IDEA-04]** Cross-assessment validation: when Conclave indexes multiple manuals from the same publisher, flag inconsistencies in scoring rubric format that might indicate transcription errors in one of them.
- **[CONCLAVE-IDEA-05]** Lens-as-plugin architecture: anyone can write a new Lens (registered via entry point); ecosystem grows. Tenth-party Lenses for specialized domains (math notation, music notation, etc.).
- **[CONCLAVE-IDEA-06]** Diff mode: when re-indexing a previously-indexed assessment, show the diff between old and new consensus. Helps validate that improvements to Lenses don't introduce regressions.
- **[CONCLAVE-IDEA-07]** Hybrid Lenses: a "PdfText + StructuredHeuristic" combined Lens that runs both internally and emits a single voted output. Enables hierarchical ensembles.
- **[CONCLAVE-IDEA-08]** Conclave-as-Curator-validator: Curator's `curator_validate_file` plugin slot could call Conclave to validate an assessment manual matches its KB extraction. If the re-extracted content disagrees with what Curator has on file, flag it.
- **[CONCLAVE-IDEA-09]** Active learning: Lenses that fail systematically on certain content types feed back to their training data (where applicable for ML-based Lenses) for retraining cycles.
- **[CONCLAVE-IDEA-10]** Multi-modal inputs: extend beyond PDFs to video/audio (clinical training videos), Word docs (test administration scripts), spreadsheets (norm tables already in structured form). Each input type has its own Lens roster.

---

## Honest "this isn't worth doing" notes

- **CONCLAVE-NOT-IDEA-01:** Building all 9 Lenses before any are usable. Phase 1's two-Lens version should ship first, prove the concept on a test assessment, get feedback. Premature optimization is the bigger risk than under-engineering.
- **CONCLAVE-NOT-IDEA-02:** Trying to make Conclave work without Curator's MCP server. The server unblocks clean Stage 1 integration; without it, Conclave needs its own file-walking code which duplicates Curator's. Sequence: Curator MCP server first, then Conclave starts.
- **CONCLAVE-NOT-IDEA-03:** Letting Conclave become an LLM-orchestration framework. It's an indexer; the LLM Lenses are tools, not the centerpiece. If the system starts feeling like LangChain-with-voting, scope creep has won.

---

*End of `CONCLAVE_PROPOSAL.md` v0.1. Targeted ~3000 words. Subject to OQ-1 through OQ-8 resolution before any build work begins. Track open questions in `ECOSYSTEM_DESIGN.md` §7 alongside DE-1 through DE-13.*
