---
title: Privacy
description: What ReadAnyDoc does with your documents and data.
---

# Privacy

ReadAnyDoc is open-source software that runs on your own computer or server. The project does not run a service and
does not receive your documents, results, API keys or usage.

- **Your documents** are read on your machine and sent only to the model provider you configure (Anthropic by
  default, OpenAI, or a self-hosted OpenAI-compatible server), under your own account and that provider's data terms.
  Point it at a self-hosted model to keep documents inside your network.
- **API keys** are read from your environment and are never written to logs, results or job records.
- **The server** (`readanydoc serve`) keeps uploads and results on your disk for the configured time and then deletes
  them; see [Security](../deploy/security.md).
- **The MCP server and the Claude Code plugin** read files on your computer only when your assistant calls a tool, and
  send them only to your model provider.
- **This website** may count page views with GoatCounter, which sets no cookies and collects no personal data.

Questions: [GitHub Discussions](https://github.com/arpitpatel25/ReadAnyDoc/discussions).
