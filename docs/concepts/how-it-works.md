# How it works

Every page goes through the same sequence of stages (`readanydoc/pipeline/`). The model does the reading;
ReadAnyDoc does what a model alone does not: it checks the work, finds where every value sits, and returns it in
the shape you asked for.

| Stage | What happens | Model calls |
|---|---|---|
| **Intake** | Detect the file type, decrypt protected PDFs, convert Office and email files, split into pages | none |
| **Triage** | Digital or scanned, rotated or not, is the PDF's own text layer trustworthy | none |
| **Layout** | A small open layout model (Docling "heron", ONNX on CPU) finds tables, forms, figures, headers and footers | none |
| **Read** | The vision model reads each page at the largest image size it keeps. Scanned pages get zoomed strip re-reads; a character changes only when two zoomed reads agree against the first read. Handwritten pages go to a stronger model. | 1 per page, more for scans and handwriting |
| **Verify** | Table grids and printed totals are checked without a model; problems become warnings and confidence flags | none |
| **Ground** | Every word and table cell is matched to its position on the page (PDF text layer or OCR), block types are set, form fields are paired | none |
| **Assemble** | Pages are joined, tables that continue across pages are linked | none |
| **Shape** | The output you asked for: blocks, Markdown, chunks, table formats, Reducto's JSON | none |

## The rules behind it

- **The model's text is not changed without independent evidence.** A second zoomed read that agrees, the PDF's own
  text, or a checked total. Everything else is recorded as evidence and confidence, never guessed.
- **Never worse than the model alone.** Every rule that helped somewhere and hurt elsewhere carries a guard.
  Ideas that did not beat a plain model read on the full benchmarks were dropped. See
  [How we built it](../story.md).
- **Route by what a page looks like, not by document type.** Digital or scanned, tables, handwriting. No templates.

## Model-only mode

`model-only` runs Intake, the single page Read and Shape. No layout model, no re-reads, no second model. See
[Modes](modes.md).
