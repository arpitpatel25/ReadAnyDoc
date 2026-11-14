# Modes: engine and model only

ReadAnyDoc ships two configurations. Both are exactly the configurations measured in the
[benchmarks](../resources/benchmarks.md), and a test fails if either one changes without new measurements.

| | `engine` (default) | `model-only` |
|---|---|---|
| Page reading | Claude Haiku 5.5, high effort | Claude Haiku 5.5, high effort |
| Layout model, block types and positions | yes | no |
| Zoomed re-reads of scanned pages | yes | no |
| Handwritten pages to a stronger model | Claude Sonnet 5.5 | no |
| Table checks, printed totals | yes | no |
| Word-level citations | yes | block level only |
| Typical cost per page | $0.003 to $0.005 | $0.001 to $0.002 |
| Typical time per page | 20 to 50 s | 8 to 28 s |
| Needs `readanydoc[engine]` | yes | no |

## When to use which

- **engine** for anything a person will check or a system will trust: forms, invoices, statements, contracts,
  scans, documents where you need positions and citations.
- **model-only** for large volumes of clean digital text where you need the words and tables, not the layout,
  and cost or speed matters most.

## Measured difference

On the complete public benchmarks the engine is clearly ahead of the model alone on page layout (+23 to +35 points),
form question and answer pairs (+5.8), headers and footers (+10.8) and the olmOCR-bench overall score (+2.0); level on
tables, plain text and long extraction. Details in [Benchmarks](../resources/benchmarks.md).

## Selecting a mode

=== "Python"

    ```python
    readanydoc.parse("doc.pdf", mode="model-only")
    ```

=== "CLI"

    ```bash
    readanydoc parse doc.pdf --mode model-only
    ```

=== "Server"

    ```bash
    READANYDOC_PROFILE=model-only readanydoc serve
    ```

    Reducto's `settings.model` also maps: `r-1` runs the configured profile, `r-1-mini` and `legacy` run its cheaper
    tier (`engine` becomes `model-only` at medium effort). See [Compatibility](../migrate/compatibility.md).
