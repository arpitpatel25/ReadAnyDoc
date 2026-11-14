# Set up with your coding agent

Copy this prompt into Claude Code, Codex, Cursor or any coding agent. It installs ReadAnyDoc, checks it on one of
your files and wires it into your project the way you choose (Python, HTTP server, Reducto replacement or MCP tools).

```text
Set up ReadAnyDoc in this project. Follow the instructions at
https://raw.githubusercontent.com/arpitpatel25/ReadAnyDoc/main/AGENT_SETUP.md
```

The agent asks you before it installs system packages, and it never asks you to paste your API key into the chat:
you add `ANTHROPIC_API_KEY` to `.env` yourself.

??? note "What the agent follows (AGENT_SETUP.md)"

    --8<-- "AGENT_SETUP.md"
