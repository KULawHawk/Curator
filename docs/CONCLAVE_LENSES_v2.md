# Conclave Lens Roster v2 — Expanded with 2025-2026 State of the Art

**Status:** v0.2 addendum to `CONCLAVE_PROPOSAL.md`
**Date:** 2026-05-08
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

| Preset | Lenses | Use case | Wallclock per assessment* |
|---|---|---|---|
| **Triage** (3) | PdfText + StructuredHeuristic + OcrTesseract | First-pass, "is this content extractable?" | ~5 min |
| **Cheap-and-fast** (5) | + OcrPaddle + TableSurgeon | Routine indexing where cost matters | ~15 min |
| **Balanced** (7) | + MarkerPdf + VisionLocal | Default for new assessments | ~30 min |
| **Full ensemble** (12) | + GotOcrUnified + NougatScience + MinerU + VisionClaude + ColPaliVerify | High-stakes assessments where ≥99.5% accuracy is required | ~60-90 min |
| **Custom** | User-configurable | Anything else | — |

*assuming a typical 150-page assessment manual on a workstation with 16GB GPU. Actual time varies wildly with content.

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

- **OQ-9.** Marker vs MinerU as primary "comprehensive PDF-to-Markdown" Lens — include both for diversity, OR pick one? (Recommendation: include both; their orchestration differences are exactly the kind of method diversity ensembles benefit from.)
- **OQ-10.** Validation Lens (ColPaliVerify) vote weight semantics — should "not grounded" reduce other Lenses' weight, or just flag for review? (Recommendation: flag for review; reducing weights gets complicated fast.)
- **OQ-11.** Lens version pinning policy — pin to specific versions for reproducibility, or allow auto-update for accuracy improvements? (Recommendation: pin to tested versions; bump per-Lens version requires re-running the validation suite.)
- **OQ-12.** Ground-truth assessment for benchmarking — which assessment serves as the canonical "we know what extraction looks like" reference? (Recommendation: use Jake's already-indexed RCS Vol. 1 since it has the most curation history.)

---

## Re-validation calendar

The roster should be reviewed:
- **At Conclave build start** — full re-validation pass; libraries may have moved, models may have improved.
- **Quarterly during Conclave development** — quick check for major releases.
- **Annually after Conclave ships** — full re-validation; consider deprecations.
- **On demand** — when a major model release lands (e.g., GPT-5, Claude Sonnet 5, Llama 4 Vision).

---

*End of `CONCLAVE_LENSES_v2.md`. Supersedes `CONCLAVE_PROPOSAL.md` §3 Lens roster; original proposal v0.1 retained for historical reference.*
