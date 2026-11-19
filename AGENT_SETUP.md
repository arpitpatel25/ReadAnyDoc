# Set up ReadAnyDoc: instructions for a coding agent

You are a coding agent (Claude Code, Codex, Cursor or similar) and the user asked you to set up ReadAnyDoc, an
open-source document parser and extractor (PDFs, scans, photos, Office files to Markdown and typed data with
citations). Follow these steps in order. Ask the user only where a step says so.

## 1. Check the machine

- Python 3.10 or newer: `python3 --version`. If missing, tell the user and stop.
- Prefer the project's own tool: `uv` if the project has `uv.lock`, `poetry` if `poetry.lock`, otherwise `pip` in
  the project's virtual environment. Do not install into the system Python.
- Optional: Tesseract OCR improves scanned pages (`brew install tesseract` on macOS,
  `sudo apt install tesseract-ocr` on Debian/Ubuntu). Ask before installing system packages.

## 2. Install

```bash
pip install "readanydoc[engine]"        # or: uv add "readanydoc[engine]"
```

`engine` adds the local layout model and OCR (CPU only). The first run downloads the layout weights once
(100-200 MB, into `~/.cache/readanydoc/`); tell the user it is a one-time wait.

## 3. API key

ReadAnyDoc calls Claude Haiku 5.5 with the user's own key.

- If `ANTHROPIC_API_KEY` is already set in the environment or the project's `.env`, use it. Do not print it.
- Otherwise ask the user to add it themselves to `.env` (create the file if needed and make sure `.env` is in
  `.gitignore`). Never ask them to paste the key into the chat, and never write a key into source code.
- OpenAI or a local OpenAI-compatible model can be used instead: see
  https://arpitpatel25.github.io/ReadAnyDoc/docs/concepts/models/.

## 4. Check that it works

Use any small PDF or image the user has, or ask for one. Then:

```bash
readanydoc parse path/to/file.pdf --markdown | head -40
```

Text and tables in Markdown mean the setup works. A run on one page costs well under one cent. If it fails, see
https://arpitpatel25.github.io/ReadAnyDoc/docs/resources/faq/.

## 5. Ask how they want to use it

Ask the user which one fits, then do only that one. For a quick look first, they can also try the Colab notebook:
https://colab.research.google.com/github/arpitpatel25/ReadAnyDoc/blob/main/examples/quickstart.ipynb

1. **In Python code**: write a small example in their project.

    ```python
    import readanydoc
    from pydantic import BaseModel

    class Invoice(BaseModel):
        number: str
        total: float

    out = readanydoc.extract("invoice.pdf", schema=Invoice)
    print(out.data, out.citations["total"], out.warnings, out.usage.cost_usd)
    ```

    Reference: https://arpitpatel25.github.io/ReadAnyDoc/docs/reference/python/

2. **As an HTTP service** (any language, or a team server): `readanydoc serve` (port 8000), or Docker with the
   repository's `docker-compose.yml`. Set `READANYDOC_API_KEY` so the server needs a bearer token.
   Reference: https://arpitpatel25.github.io/ReadAnyDoc/docs/get-started/server-quickstart/

3. **Replacing Reducto**: keep their Reducto SDK code, start `readanydoc serve`, and change the client's
   `base_url` to it. Guide: https://arpitpatel25.github.io/ReadAnyDoc/docs/migrate/from-reducto/

4. **As tools for an AI assistant (MCP)**: `pip install "readanydoc[engine,mcp]"`, then register the command
   `readanydoc mcp` with their client and pass `ANTHROPIC_API_KEY` in its environment. For Claude Code:

    ```bash
    claude mcp add readanydoc -e ANTHROPIC_API_KEY=... -- readanydoc mcp
    ```

    In Claude Code the plugin is simpler (needs `uv`): `/plugin marketplace add arpitpatel25/ReadAnyDoc`, then
    `/plugin install readanydoc@readanydoc`. Guide (Claude Desktop, Cursor and others):
    https://arpitpatel25.github.io/ReadAnyDoc/docs/get-started/mcp/

5. **In a LangChain or LlamaIndex pipeline**: `pip install "readanydoc[engine,langchain]"` (or `llamaindex`) and use
   `readanydoc.integrations.langchain.ReadAnyDocLoader` / `readanydoc.integrations.llamaindex.ReadAnyDocReader`.
   Guide: https://arpitpatel25.github.io/ReadAnyDoc/docs/guides/integrations/

6. **To test it against what they use now** (Reducto, a plain model call) on public test sets or their own
   documents: `readanydoc bench list`, then for example
   `readanydoc bench run invoices --systems readanydoc,plain:claude-haiku-5-5,reducto --limit 10`, or
   `readanydoc bench mine <folder> --schema <schema.json> [--answers <folder>]`. It shows a rough cost and asks
   before spending; show the user that cost and run only after they agree. Through MCP: `list_test_sets` and
   `run_test` (`confirm=true` only after the user agrees). Guide:
   https://arpitpatel25.github.io/ReadAnyDoc/docs/guides/testing/

## 6. Finish

Tell the user what was installed, where the key lives, which way they chose, and how to run it again. Point them
to the community for questions: https://github.com/arpitpatel25/ReadAnyDoc/discussions

Facts to get right when you explain it: only Claude Haiku 5.5 (Sonnet 5.5 for handwriting) is benchmarked;
confidence is `high` or `low`, not a probability; always check `warnings`.
