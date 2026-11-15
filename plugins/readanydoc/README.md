# ReadAnyDoc plugin for Claude Code

Parse PDFs, scans, photos and Office files into Markdown, and extract typed fields with a citation for every value,
right inside Claude Code.

```text
/plugin marketplace add arpitpatel25/ReadAnyDoc
/plugin install readanydoc@readanydoc
```

Needs [uv](https://docs.astral.sh/uv/) (the server runs with `uvx`, nothing else to install) and `ANTHROPIC_API_KEY`
in the environment you start Claude Code from; the server reads it from there and sends it only to Anthropic.
The plugin pins ReadAnyDoc to an exact commit of this repository. It runs without the local layout model and OCR
(the `engine` extra); for those, use `pip install "readanydoc[engine]"` and `claude mcp add readanydoc -- readanydoc mcp`. The first run downloads the package and the layout model once.

Then ask, for example: *"Extract the invoice number, date and total from ~/Downloads/invoice.pdf with citations"*.

Includes the `readanydoc` MCP server (tools `parse_document`, `extract_fields`) and a `read-documents` skill that
tells Claude when and how to use them. More: https://arpitpatel25.github.io/ReadAnyDoc/
