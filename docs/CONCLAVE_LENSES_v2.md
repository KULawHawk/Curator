# Conclave Lens Roster v2 — Expanded with 2025-2026 State of the Art

**Status:** v0.3 — RATIFIED 2026-05-14 by Jake Leese (chat reply "yes" to the v0.3 amendment proposal at `Conclave/docs/proposals/LENSES_v2_v03_PROPOSED_AMENDMENT.md`).
**Date:** 2026-05-08 (v0.2); 2026-05-14 (v0.3 amendment)
**Authority:** Subordinate to `Atrium/CONSTITUTION.md` and `CONCLAVE_PROPOSAL.md` v0.1.
**Knowledge cutoff disclosure:** This roster reflects my knowledge as of January 2026. Fast-moving categories (frontier VLMs, OCR APIs) need re-validation when Conclave build begins. See §4 below for the validation methodology.

---

## Why this addendum exists

The original `CONCLAVE_PROPOSAL.md` v0.1 proposed 9 Lenses. Jake asked: "are those the best 9, or could we expand if each truly has a unique way of approaching sources?"

The honest answer: 9 is a reasonable starting set but missing four genuinely distinct extraction approaches that have matured in 2024-2025. With those additions and one consolidation, the v2 roster is **12 Lenses**.

Each Lens earns its slot by the **distinctness criterion**: its failure modes must be uncorrelated with at least 80% of the other Lenses on the same content. If two Lenses fail on the same content for the same reasons, they don't add ensemble value — they're voting redundancy.

---

## The Lens distinctness criterion (formal)

For Lens L to earn a slot in the ensemble:

1. **Distinct method.** L uses an extraction approach not used by any other Lens (e.g., transformer-unified vs. CRNN-pipeline vs. native-text-layer vs. VLM).
2. **Distinct failure mode.** L's typical failures occur on inputs where most other Lenses succeed. (E.g., Tesseract fails on multi-column scans where Marker succeeds; both belong because they fail in different places.)
3. **Distinct cost profile.** L offers a different cost/quality tradeoff (free-and-fast / free-but-slow / paid-and-accurate). A Lens whose cost profile duplicates an existing Lens's adds nothing.

Lenses that fail this check get consolidated or replaced. Lenses that pass it earn their seat at the table.

---

## v2 Roster: 12 Lenses

### Extraction Lenses (11)

#### Lens 1: PdfText — Native PDF text-layer extraction
- **Library:** pdfplumber (jsvine/pdfplumber, MIT) [stable since 2018; mature]
- **Method:** Reads PDF text objects directly. Preserves font metadata, position, color.
- **Distinct because:** Only Lens that achieves zero-loss on born-digital PDFs.
- **Failure mode:** Returns nothing on scanned pages. Loses content embedded in images.
- **Cost:** ~100ms/page. Free.
- **Re-validate:** Stable; low priority for re-validation.

#### Lens 2: OcrTesseract — Traditional OCR via Tesseract
- **Library:** pytesseract (madmaze/pytesseract, GPL-2 wrapper around Apache-2 Tesseract) [stable since 2007]
- **Method:** Rasterize page → Tesseract LSTM-based OCR → reading-order reconstruction from bounding boxes.
- **Distinct because:** Battle-tested on noisy historical documents; different ML lineage than transformer OCRs.
- **Failure mode:** Multi-column reading-order breaks. Dense tables collapse.
- **Cost:** ~3-5s/page. Free.
- **Re-validate:** Stable.

#### Lens 3: OcrPaddle — Modern OCR with layout analysis
- **Library:** PaddleOCR (PaddlePaddle/PaddleOCR, Apache-2) [actively developed; 2024-2025 versions add table recognition]
- **Method:** Ensemble of detection + recognition models trained on 80+ languages with explicit layout analysis.
- **Distinct because:** Better multi-column reading order than Tesseract; supports CJK well; integrated layout-aware model.
- **Failure mode:** Mathematical notation; very low-quality scans.
- **Cost:** ~5-8s/page. Free.
- **Re-validate:** Active development; check current version when building.

#### Lens 4: GotOcrUnified — Transformer-unified OCR
- **Library:** GOT-OCR 2.0 (Stepfun, 2024). [Validate availability: HuggingFace `stepfun-ai/GOT-OCR2_0`]
- **Method:** Single end-to-end transformer that handles text, math formulas, sheet music, geometric shapes, charts. No separate detection / recognition / layout pipeline.
- **Distinct because:** Different architecture from Tesseract (CRNN-based) and Paddle (multi-stage). Single-model approach has different error profile.
- **Failure mode:** Less stable on extreme low-quality scans (lacks dedicated denoising step).
- **Cost:** ~5-15s/page on GPU. Free; needs ~8GB VRAM for usable speed.
- **Re-validate:** Newer model; verify current state when building. Likely worth it.

#### Lens 5: MarkerPdf — General PDF-to-Markdown specialist
- **Library:** Marker (VikParuchuri/marker, GPL-3 with commercial-use exception) [actively developed 2024-2026]
- **Method:** Combines text extraction + OCR + layout analysis + ML reading-order in optimized pipeline.
- **Distinct because:** Designed for the specific PDF→Markdown task; tuned for academic/scientific layouts.
- **Failure mode:** Heavily scanned-only docs; non-Latin scripts.
- **Cost:** ~10-15s/page on GPU. Free.
- **License watch:** GPL-3 with commercial exception — read terms before commercial deployment.
- **Re-validate:** Frequent updates; check current version.

#### Lens 6: NougatScience — Scientific paper specialist
- **Library:** Nougat (Meta, 2023) [HuggingFace `facebook/nougat-base`]
- **Method:** Vision encoder + decoder transformer trained specifically on academic papers from arXiv.
- **Distinct because:** Equation-aware (preserves LaTeX). Preserves academic-paper structure (abstracts, citations, figure references).
- **Failure mode:** Non-academic content; modern non-arXiv layouts.
- **Cost:** ~10-20s/page on GPU. Free.
- **Re-validate:** Lower priority — well-established.

#### Lens 7: TableSurgeon — Table specialist (combined approach)
- **Libraries:** Camelot (camelot-dev/camelot, MIT) + table-transformer (microsoft/table-transformer, MIT)
- **Method:** Camelot extracts text-layer tables; table-transformer detects + extracts tables from page images. Run both; pick higher-confidence per table.
- **Distinct because:** Specialist tools beat generalists on tables. Critical for psychometric assessments (norm tables, scoring rubrics, percentile lookups).
- **Failure mode:** Returns nothing on non-tabular content (that's fine — other Lenses cover it).
- **Cost:** ~2-4s/page (only on pages flagged as containing tables).
- **Re-validate:** Both stable; periodically check for table-transformer model updates.

#### Lens 8: VisionClaude — Frontier VLM (cloud)
- **Library:** anthropic Python SDK (anthropics/anthropic-sdk-python, MIT)
- **Method:** Render page at 300 DPI → send to Claude (Sonnet/Opus per cost tier) with structured extraction prompt → parse response.
- **Distinct because:** Best-in-class semantic understanding. Catches subtle context that pure-extraction Lenses miss (italics conveying emphasis, footnote relationships, table column intent).
- **Failure mode:** Cost per page; rate limits; possible hallucination on dense text (mitigated by grounding prompts).
- **Cost:** ~$0.005-0.05 per page depending on model. Slow (~3-8s).
- **Smart use:** Invoke only on disagreement among other Lenses, OR on pages flagged as containing complex visual elements. Budget cap configurable per assessment.
- **Re-validate:** Frontier model; will improve significantly. Re-prompt may be needed for new model versions.

#### Lens 9: VisionLocal — Open-weight VLM (offline)
- **Library:** Qwen2-VL-7B (QwenLM/Qwen-VL, Apache-2) OR Llama-3.2-Vision-11B (Meta, custom commercial license)
- **Method:** Same as VisionClaude using local model.
- **Distinct because:** Provides redundancy AND offline-capability (per Constitution Aim 3 self-sufficiency). When VisionLocal and VisionClaude agree, very high confidence.
- **Failure mode:** Lower accuracy ceiling than frontier APIs.
- **Cost:** Free; ~15-30s/page on GPU (8-16GB VRAM).
- **Re-validate:** Frontier of open-weight VLMs is moving fast; check current best when building. Mistral, Pixtral, InternVL3 are alternatives.

#### Lens 10: MinerU — Comprehensive integrated pipeline
- **Library:** MinerU (Shanghai AI Laboratory, opendatalab/MinerU, AGPL-3) [actively developed 2024-2025; license watch item]
- **Method:** Comprehensive pipeline including layout detection, formula recognition, table recognition, OCR fallback, reading order, and Markdown emission. Different orchestration philosophy than Marker.
- **Distinct because:** Different orchestration choices than Marker (e.g., dedicated formula recognizer); different failure profile.
- **Failure mode:** Heavyweight install; AGPL-3 license requires care for commercial integration.
- **Cost:** ~20-30s/page on GPU.
- **License watch:** AGPL-3. Compatible with personal / open-source use; restrictive for proprietary integration.
- **Re-validate:** Active development; current version matters. Verify license at build time.
- **Note:** Could be alternative to Marker rather than addition. Decision OQ-9 (new): include both for ensemble diversity, OR pick one to reduce overhead?

#### Lens 11: StructuredHeuristic — Domain pattern matcher
- **Library:** Pure Python regex + custom rules (no external dep)
- **Method:** Encodes known patterns from psychometric assessment literature: Likert scales, item lists, scoring rubrics ("Score 0 if...", "Score 1 if..."), percentile tables, normative data tables, citation styles.
- **Distinct because:** When patterns match, near-100% precision. Domain-specific knowledge no general extractor has.
- **Failure mode:** Returns nothing on unknown patterns (that's fine).
- **Cost:** <100ms/page. Free.
- **Re-validate:** Pattern library grows over time; not really a re-validation question.

#### Lens 14: HtrLens — Handwriting recognition specialist
- **Library:** Kraken (mittagessen/kraken, Apache-2) PRIMARY; TrOCR-handwritten (microsoft/trocr-base-handwritten, MIT) FALLBACK. Both actively developed 2024-2026.
- **Method:** Specialized handwriting recognition model. Kraken uses a recurrent network architecture trained on historical and contemporary handwritten text; TrOCR uses a transformer encoder-decoder. The Lens runs the primary; on `accepts_text_layer=False` pages where Kraken's confidence falls below threshold, the secondary fires as cross-check.
- **Distinct because:** Only Lens trained specifically on handwritten content. LENS-02/03/04/05/06/10 are all trained on printed text and produce garbage or empty output on handwriting. Failure modes are uncorrelated with the entire printed-OCR cluster.
- **Failure mode:** Returns nothing or low-confidence output on purely printed content (other Lenses cover it). Variable accuracy on highly stylized cursive or non-Latin scripts. Calibration corpus is the gating concern — see Test Corpus Audit.
- **Cost:** ~10-20s/page on GPU. Free.
- **License watch:** Apache-2 (Kraken) + MIT (TrOCR). Clean — CL-37 LICENSE WATCH still applies (build-time re-fetch + SHA256 verification per the now-binding pattern).
- **Re-validate:** HTR field moves fast; check current SOTA when building (DAN, OCRopus alternatives, McMaster-OCR for medical-form handwriting are emerging candidates).
- **Source-corpus implication:** v1.0 release-gate test corpus MUST include handwritten samples (clinician marginalia, handwritten test responses, annotated scanned manuals). See `TEST_CORPUS_AUDIT_2026-05-14.md` (WC-005, pending).

#### Lens 15: FormFieldLens — Structured-form / response-sheet specialist
- **Library:** Donut (clovaai/donut, MIT) PRIMARY; LayoutLMv3 (microsoft/LayoutLMv3, MIT) FALLBACK fine-tuned for form-field extraction. Both 2024-2026 active.
- **Method:** Document-AI model that detects and extracts structured form regions — checkboxes (filled vs unfilled), fillable text fields (blank vs filled), score-block tables, Likert response grids, multi-page form continuation. Emits structured (field_name, field_value, confidence) tuples rather than flat text.
- **Distinct because:** Form structure is a distinct extraction task. Generalist OCR Lenses produce "checkbox: x checkbox: o" without semantic mapping; FormFieldLens emits "item_3_response: agree, item_4_response: strongly_disagree". Critical for scored assessments where the response sheet IS the data.
- **Failure mode:** Returns nothing on non-form content (other Lenses cover it). May confuse decorative tables with forms — mitigated by pre-invocation quality gate (CL-30 applies: this is a single-model end-to-end Lens, so the gate is mandatory).
- **Cost:** ~5-10s/page on GPU.
- **License watch:** MIT for both candidates. Clean.
- **Re-validate:** Document-AI field is active; check for newer fine-tuned models (Donut-v2, LayoutLMv4 if released, Textract-equivalent open models).
- **Source-corpus implication:** v1.0 release-gate test corpus MUST include response sheets, scoring forms, fillable PDFs (blank + filled pairs are calibration-pair candidates). See `TEST_CORPUS_AUDIT_2026-05-14.md` (WC-005, pending).

#### Lens 16: HierarchyLens — Document structure / TOC / cross-reference resolver
- **Library:** PyMuPDF outline extraction (pymupdf/pymupdf, AGPL-3 — subprocess-isolation per CL-35 required) PRIMARY for TOC / outline; pypdf (py-pdf/pypdf, BSD-3-Clause) FALLBACK + custom cross-reference resolver in pure Python.
- **Method:** Three-pass: (1) extract PDF outline / TOC from metadata or text-layer reconstruction; (2) parse section numbering + heading hierarchy; (3) resolve in-text cross-references ("see Chapter 4", "Table 3.2", "Appendix B Norm Table 14") into typed pointers (section_id, table_id, figure_id). Emits the document's hierarchical tree as structured JSON; emits cross-reference edges as a graph.
- **Distinct because:** Other Lenses extract CONTENT; this Lens extracts STRUCTURE. Critical for long manuals where cross-references carry semantic load — a norm-table cited from a procedural section IS the answer to "what norms apply to this protocol", and missing the cross-ref breaks the KB graph downstream. Failure modes are wholly uncorrelated with content-extraction Lenses (different task, different signal).
- **Failure mode:** Returns minimal output on flat documents (single-section, no TOC, no cross-refs — many short questionnaires fall here). May mis-parse non-standard numbering schemes (e.g., "Chapter the Fourth"). Cross-reference grammar varies by publisher.
- **Cost:** <1s/page (lightweight; mostly metadata-driven; pure Python).
- **License watch:** PyMuPDF AGPL-3 requires subprocess isolation per CL-35 (call the `mutool` CLI, do NOT Python-import `fitz` directly) OR fall back to pypdf (BSD, clean). CL-37 LICENSE WATCH applies — note PyMuPDF's AGPL-3 has had stable text since 2016 but a license-watch is binding regardless.
- **Re-validate:** Cross-reference grammar patterns per assessment-publisher; the regex library grows over time (this Lens shares a maintenance pattern with LENS-11 StructuredHeuristic).
- **Source-corpus implication:** v1.0 release-gate test corpus MUST include deeply hierarchical manuals (3+ TOC levels, cross-volume references, manuals with index + glossary + appendices). See `TEST_CORPUS_AUDIT_2026-05-14.md` (WC-005, pending).

#### Lens 17: FormulaLens — Mathematical / LaTeX specialist
- **Library:** pix2tex (lukas-blecher/LaTeX-OCR, MIT) PRIMARY; im2latex (varies; harvardnlp/im2markup, MIT) FALLBACK. Both 2024-2026 active.
- **Method:** Image-to-LaTeX vision-encoder + sequence-decoder model. The Lens crops candidate math regions (detected via PaddleOCR layout output OR a dedicated math-region detector), runs the primary model, and emits LaTeX source. Downstream APEX renders the LaTeX or processes the formula AST.
- **Distinct because:** Math is rendered, not encoded. Mainstream OCR Lenses produce structured garbage on equations ("σ² = Σ(x-μ)²/N" extracted as "σ² = Σ(x-μ)²/N" character-by-character without preserving the equation structure). FormulaLens preserves equation structure as LaTeX which is parseable downstream. Failure modes are uncorrelated with all text-Lenses (different rendered content type).
- **Failure mode:** Returns nothing on non-math content. Variable accuracy on multi-line equations, matrices, and unusual notation (mitigated by multi-pass ensemble within the Lens — primary + fallback agreement boosts confidence). May produce LaTeX that is syntactically valid but semantically wrong on degraded sources.
- **Cost:** ~2-5s/page when math is detected; <1s/page when not (the math-region detector short-circuits cleanly on pages with no math). GPU recommended for primary throughput.
- **License watch:** MIT for both candidates. Clean.
- **Re-validate:** Math-OCR is a fast-moving field; check newer transformer models (Texify by VikParuchuri, Nougat-math, Donut-math fine-tunes).
- **Source-corpus implication:** v1.0 release-gate test corpus MUST include math-heavy content — regression equations in validity studies, scoring formulas, conversion algorithms with formal notation, statistical appendices, decision-rule equations. See `TEST_CORPUS_AUDIT_2026-05-14.md` (WC-005, pending).

### Verification Lens (1)

#### Lens 12: ColPaliVerify — Late-interaction retrieval verifier
- **Library:** ColPali (illuin-tech/colpali, MIT) [Faysse et al. 2024]
- **Method:** Render page as image → encode via vision model → for each extracted claim, query the page representation → compute "is this claim grounded in the page?" score.
- **Distinct because:** This is **verification, not extraction.** Doesn't propose its own version; instead, validates other Lenses' versions.
- **Use in voting:** A Lens output that ColPaliVerify rates as "not grounded" gets its vote weight reduced. A claim that no Lens proposes but ColPaliVerify finds in the page gets surfaced as "missing extraction."
- **Failure mode:** False positives on visually-similar but semantically-different content.
- **Cost:** ~5-10s/page on GPU.
- **Re-validate:** Newer model; verify current state.

---

## Configurable subset presets (updated)

| Preset | Lenses (count) | Roster | Use case | Wallclock per assessment\* |
|---|---|---|---|---|
| **Triage** | 3 | PdfText + StructuredHeuristic + OcrTesseract | First-pass, "is this content extractable?" | ~5 min |
| **Cheap-and-fast** | 5 | + OcrPaddle + TableSurgeon | Routine indexing where cost matters | ~15 min |
| **Balanced** | 8 | + MarkerPdf + VisionLocal + FormFieldLens | **NEW DEFAULT** for assessments with forms (the majority of psychometric content) | ~35 min |
| **Forensic** (new) | 12 | + HtrLens + HierarchyLens + FormulaLens + VisionClaude | High-content-diversity assessments — handwriting, deep structure, math, semantic-VLM confirmation | ~75 min |
| **Full ensemble** | 16 | + GotOcrUnified + NougatScience + MinerU + ColPaliVerify | Full roster except Adjudicator (which runs only on disagreement). High-stakes assessments where ≥99.5% accuracy is required + reproducibility for audit. | ~90-120 min |
| **Comprehensive** (new) | 17 | + Adjudicator (always-on) | Maximum-confidence runs where adjudication runs on every claim, not just on disagreement. Forensic / clinical / legal-review grade. | ~120-150 min |
| **Custom** | user-configurable | Per-Lens toggle via ReviewConsole LensControl panel (OQ-23 cascade) | Anything else; per-deployment AND per-document overrides | varies |

\* Wallclock estimates assume a 100-page assessment on a single GPU host; scales sub-linearly with parallel-extract-friendly Lenses.

---

## Lens evaluation methodology (for ongoing roster updates)

Conclave's roster will evolve. New extraction methods will emerge; existing ones will improve or become obsolete. The methodology for adding/replacing Lenses:

1. **Identify a candidate.** A new library, model, or technique that claims relevance.
2. **Distinctness check.** Does it satisfy all three criteria from §"The Lens distinctness criterion"?
3. **Benchmark against the existing roster.** Run on a held-out assessment with ground truth. Compute:
   - Pairwise correlation of failures with each existing Lens (must be <0.8 for at least one)
   - Marginal accuracy contribution to the ensemble (must be >0.5%)
   - Cost per page in time + dollars (must fit a preset profile)
4. **License check.** Compatible with intended deployment? AGPL/GPL trigger careful evaluation.
5. **Activity check.** Library actively maintained? Last commit within 6 months? Issues responded to?
6. **Document.** New Lens entry in this doc following the existing format.
7. **Bump Conclave version.** Adding/removing a Lens is a minor version bump.

When a Lens consistently fails the distinctness check (its failures correlate strongly with another Lens, AND its marginal accuracy contribution is negligible), it's removed. Removal also bumps Conclave's version with a CHANGELOG entry explaining why.

---

## What's NOT in the roster (and why)

Honest exclusions, with reasoning:

- **Mistral OCR API** — paid API; would duplicate VisionClaude's role. Could replace VisionClaude if cost/quality favors it at build time.
- **Reducto / Aryn DocParse** — comprehensive paid services. Same reasoning: duplicates roles. Worth benchmarking but not adding by default.
- **Generic pypdf / pdfminer.six** — duplicates pdfplumber's role with worse API. pdfplumber wins.
- **Surya** — alternative to PaddleOCR for layout-aware extraction. Strong choice. Could replace PaddleOCR if benchmarks favor it.
- **EasyOCR** — alternative to Tesseract. Less battle-tested. Tesseract wins on stability.
- **Donut** — OCR-free document understanding. Older than GOT-OCR 2.0; mostly superseded.
- **LayoutLMv3** — earlier-generation layout-aware model. Mostly superseded by Marker / Nougat / MinerU which use it internally.
- **Generic VLMs (GPT-4V, Gemini Vision)** — could replace VisionClaude if available. Plugin architecture lets users swap.
- **Specialized music notation, math notation, chemistry notation parsers** — psychometric assessments rarely contain these. Could be added as domain-specific Lenses if the use case shifts.

---

## Open questions added by v2

In addition to OQ-1 through OQ-8 in CONCLAVE_PROPOSAL.md §9:

- **OQ-9.** Marker vs MinerU as primary "comprehensive PDF-to-Markdown" Lens — include both for diversity, OR pick one? (**RESOLVED 2026-05-14 (v0.3):** ADDITION — both included. Jake's anchor is "overbuilt by intent"; functional redundancy is intentional belt-and-suspenders per the licensing-backup rationale he articulated (GOT-OCR Apache-2 is the cleanest license-failure-mode backup for Marker GPL-3, even though functionally weaker). The 17-Lens roster locks no-drops + per-Lens toggle.)
- **OQ-10.** Validation Lens (ColPaliVerify) vote weight semantics — should "not grounded" reduce other Lenses' weight, or just flag for review? (Recommendation: flag for review; reducing weights gets complicated fast.)
- **OQ-11.** Lens version pinning policy — pin to specific versions for reproducibility, or allow auto-update for accuracy improvements? (Recommendation: pin to tested versions; bump per-Lens version requires re-running the validation suite.)
- **OQ-12.** Ground-truth assessment for benchmarking — which assessment serves as the canonical "we know what extraction looks like" reference? (Recommendation: use Jake's already-indexed RCS Vol. 1 since it has the most curation history.)
- **OQ-23 (new, 2026-05-14 v0.3).** Where does the user-facing per-Lens toggle GUI live? Recommended: cascade amend `SPEC_INFRA_ReviewConsole.md` v0.2 to add a "LensControl" panel with per-Lens enable/disable checkbox, per-Lens budget slider (for cloud-API Lenses), per-document override capability. Alternative: spin out a dedicated `SPEC_INFRA_LensControl.md` spec. WC-004 recommended the ReviewConsole cascade; a follow-up WC will execute it after `CONCLAVE_LENSES_v2.md` v0.3 ratifies.
- **OQ-24 (new, 2026-05-14 v0.3).** Should LENS-14 HtrLens use Kraken-primary or TrOCR-primary? Recommendation deferred to the per-spec authoring chat (per spoke-and-wheel — each Lens's spec-gen chat owns its library choice based on current SOTA at authoring time).
- **OQ-25 (new, 2026-05-14 v0.3).** Should LENS-16 HierarchyLens use PyMuPDF-via-subprocess (per CL-35) or pypdf-only (cleaner but less capable)? Recommendation deferred to the per-spec authoring chat.

---

## Re-validation calendar

The roster should be reviewed:
- **At Conclave build start** — full re-validation pass; libraries may have moved, models may have improved.
- **Quarterly during Conclave development** — quick check for major releases.
- **Annually after Conclave ships** — full re-validation; consider deprecations.
- **On demand** — when a major model release lands (e.g., GPT-5, Claude Sonnet 5, Llama 4 Vision).

---

## Revision log

* **2026-05-14 v0.2 → v0.3** — Adds four new Lens entries (LENS-14 HtrLens, LENS-15 FormFieldLens, LENS-16 HierarchyLens, LENS-17 FormulaLens), expands configurable subset presets (introduces "Forensic" 12-Lens preset and "Comprehensive" 17-Lens preset), surfaces OQ-23 (ReviewConsole LensControl cascade) + OQ-24 (HtrLens library choice) + OQ-25 (HierarchyLens library choice). Resolves OQ-9 (MinerU as alternative or addition?) as ADDITION per Jake's "overbuilt" anchor. Proposed in `Conclave/docs/proposals/LENSES_v2_v03_PROPOSED_AMENDMENT.md` v0.1 under WC-004 (2026-05-14 evening). Ratified by chat reply "yes". Frozen import at `Conclave/docs/imports/CONCLAVE_LENSES_v2.md` synced per `Conclave/docs/imports/IMPORTS_LOG.md` procedure same-day under Conclave WC-006.

---

*End of `CONCLAVE_LENSES_v2.md`. Supersedes `CONCLAVE_PROPOSAL.md` §3 Lens roster; original proposal v0.1 retained for historical reference.*
