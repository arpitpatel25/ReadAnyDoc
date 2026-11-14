# Use it from Claude, Cursor and other assistants (MCP)

`readanydoc mcp` runs ReadAnyDoc as an [MCP](https://modelcontextprotocol.io) server, so an AI assistant can read
your documents directly in a chat, without code.

It adds two tools that work on files on your computer:

| Tool | What it returns |
|---|---|
| `parse_document(path, mode?, pages?)` | the document as Markdown (tables as HTML), page count, warnings, cost |
| `extract_fields(path, schema, instructions?, array_extract?, mode?)` | values for a JSON Schema, each with citations (text, page box, confidence), warnings, cost |

## Install

```bash
pip install "readanydoc[engine,mcp]"
```

The server uses your own `ANTHROPIC_API_KEY` (or `OPENAI_API_KEY` for OpenAI models), passed in its environment.

## Claude Code

```bash
claude mcp add readanydoc -e ANTHROPIC_API_KEY=your-key -- readanydoc mcp
```

## Claude Desktop, Cursor, Windsurf and other clients

Add this to the client's MCP configuration (`claude_desktop_config.json`, `.cursor/mcp.json`, ...). Use the full path
of `readanydoc` (`which readanydoc`) if the client does not see your virtual environment.

```json
{
  "mcpServers": {
    "readanydoc": {
      "command": "readanydoc",
      "args": ["mcp"],
      "env": {"ANTHROPIC_API_KEY": "your-key"}
    }
  }
}
```

## Try it

Ask the assistant, for example: *"Extract the invoice number, date and total from ~/Downloads/invoice.pdf with
citations"* or *"Parse ~/Documents/report.pdf and summarise the tables on page 3"*.

## Notes

- Files are read from the local disk only; the server does not fetch URLs.
- A parse can take from a few seconds to minutes on long documents. `mode="model-only"` is faster.
- Check `warnings` in every result. Confidence is `high` or `low`, not a probability.
