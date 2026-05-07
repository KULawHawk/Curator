# APEX Information Request — for the Curator Ecosystem Design

**Purpose:** This is a prompt to give to the APEX project Claude session.
The Curator-side Claude needs information about APEX's architecture and
conventions to design clean integration between APEX and the Curator
ecosystem (Curator + Umbrella + Nestegg).

**Usage:** Paste the section below "PROMPT BEGINS" into the APEX project
chat. The APEX session will respond with structured information that you
paste back into the Curator chat for the Curator-side Claude to ingest.

---

## PROMPT BEGINS — copy from here

I need information about APEX's current architecture to support a
forward-looking integration design with another tool I'm building
(Curator — a file-knowledge-graph tool at
`C:\Users\jmlee\Desktop\AL\Curator\`). The Curator-side Claude is
designing how Curator and APEX could integrate cleanly when both are
installed, while remaining fully functional standalone.

This is a **design / inventory request**, not a request to change
anything in APEX. No APEX modifications are being asked for.

**Per the APEX high-rigor protocol**: please respond with what's
documented in project knowledge. Where a section can't be answered from
documented sources (Constitution, Protocol, Whiteboards, Roadmap, source
code in project knowledge), mark that section `[NOT IN PROJECT KNOWLEDGE]`
rather than inferring. Where APEX has documents that contain the answer,
**cite the document name + section** rather than reproducing huge chunks.

Please structure the response as a single self-contained markdown
document I can paste back as-is. Use code blocks for any technical
specifications (CLI invocations, config schemas, API surfaces).

---

### 1. Architecture overview

- What is APEX? One-paragraph description of purpose and scope.
- What are the subAPEXs (current + planned)? Brief description per subAPEX.
- What's the relationship between APEX (the governance layer) and the
  subAPEXs (the work units)?
- For each subAPEX, what does it do at a conceptual level?

### 2. The subAPEX2 (Indexer) overlap question — CRITICAL

This is the most important section for integration design.

- What does the Indexer (subAPEX2) do, technically?
- What does it index, and how (file walks? database queries? API calls?)?
- What persistent storage does it use (database, files, in-memory)?
- Does it overlap with Curator's role of indexing/scanning/hashing files
  and tracking lineage between them? If yes, **where exactly** is the
  overlap — file enumeration, hashing, classification, lineage detection,
  or something else?
- Could one of these be a consumer of the other? E.g.:
  - APEX's Indexer queries Curator for file inventory + hashes
  - Curator delegates to APEX's Indexer for some categorization
  - They run side-by-side with different scopes
- What would be lost (if anything) if APEX's Indexer were retired in
  favor of Curator? What would be gained?

### 3. APEX's external surface

For each that exists, describe it:

- **CLI**: top-level commands, common subcommands, exit code conventions.
- **MCP server**: does APEX expose one? Tools and parameters.
- **Python library API**: importable from `curator` or other tools?
  Top-level modules + their public functions.
- **REST or other programmatic access**: endpoints, auth.

For each: is the surface stable/documented, or in flux?

### 4. APEX's conventions (this matters most for SIP design)

The Curator-side is proposing a Suite Integration Protocol (SIP) that
shares conventions across Curator/APEX/Umbrella/Nestegg. To know what to
align with, I need to know what APEX already does:

- **Audit log**: does APEX maintain one? Format (JSON? lines? schema)?
  Storage location?
- **Logging library**: which one (loguru, stdlib logging, custom)? What
  format (structured JSON, text)?
- **Configuration files**: format (TOML, YAML, JSON)? Schema/structure?
  Where do user-facing config files live?
- **Plugin / extension model**: does APEX have one? How are plugins
  registered? Entry points, manual registration, or none?
- **Naming/namespacing**: any conventions for new APEX-related
  packages or files (`apex.*`, `apexplug.*`, `apex_<verb>` CLI commands)?
- **Health check or status command**: does APEX have a way to report
  "am I working correctly?" via CLI / API?

### 5. Constitution + Protocol summary (as it relates to integration)

- Briefly summarize APEX's Constitution (one paragraph).
- Briefly summarize the Protocol (one paragraph).
- Identify any constraints either places on integrations with external
  systems. For example: are there rules about what APEX may import / be
  imported by, or whether APEX may call out to external services?

### 6. What Curator could surface for APEX

If Curator exposed an API like:

```python
# Hypothetical Curator API
curator.search(filter=FileQuery(...))   -> list[FileEntity]
curator.lineage(file_id)                 -> list[LineageEdge]
curator.bundles(file_id)                 -> list[BundleEntity]
curator.classify(path) -> FileClassification
```

What would APEX's subAPEXs use it for? Be concrete:

- **TAT** might want X (e.g., "find all my TAT-related files modified
  this week, with their bundle memberships").
- **FAP** might want Y.
- **RCS** might want Z.
- **Indexer (subAPEX2)** — see §2.

### 7. What APEX could provide to Curator

Conversely: are there APEX capabilities Curator could leverage?

- Does APEX have classification logic Curator's plugins could call?
- Does APEX have validation / quality-check logic that maps to Curator's
  `curator_validate_file` plugin hook?
- Does APEX have governance rules that should veto Curator operations
  (`curator_pre_trash`, `curator_pre_restore` hooks)?
- Does APEX produce derived metadata that Curator could store on
  FileEntity flex attrs?

### 8. Anything I haven't asked but should know

Open-ended. Examples:
- APEX-specific terminology I should learn before designing the integration.
- Pending APEX architectural changes that would alter the integration
  surface.
- Other tools/projects in the same ecosystem that the integration should
  account for.
- Strong opinions about what Curator should and shouldn't try to do.

---

### Output format

Please return a single markdown document with sections 1-8. For each
section, lead with the answer; cite supporting documents (Constitution
§N, Whiteboard W##, Roadmap v2.X §Y) where available. If a section is
`[NOT IN PROJECT KNOWLEDGE]`, say so explicitly rather than guessing.

Total target length: 1500-3000 words. Concise but complete.

## PROMPT ENDS — copy ends here

---

## After the APEX session responds

Paste the response back into the Curator chat. The Curator-side will:

1. Map the APEX integration surface against Curator's existing primitives
2. Update `DESIGN_PHASE_DELTA.md` and the proposed SIP with concrete
   alignment points
3. Identify the smallest first-integration milestone (e.g., "Curator's
   `query` MCP tool callable from APEX's TAT subAPEX")
4. Flag any APEX architectural choices that the SIP can't bridge, with
   options for resolution
