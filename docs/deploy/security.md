# Security

## Defaults

| Setting | Default | Why |
|---|---|---|
| `READANYDOC_API_KEY` | unset: the server is open | Fine on your laptop. **Set it before the port is reachable by anyone else.** |
| `READANYDOC_BLOCK_PRIVATE_URLS` | on | URL inputs that resolve to private or loopback addresses are refused, so callers cannot make the server fetch internal services |
| `READANYDOC_ALLOW_LOCAL_PATHS` | off | Server-side file paths as `input` are refused |
| `READANYDOC_DOCUMENT_TIMEOUT_SECONDS` | 1800 | No single document can tie up a worker forever |
| `READANYDOC_MAX_UPLOAD_MB` | 100 | Upload and download size limit |

## Keys and data

- Provider keys are read from the environment and never written to logs, results or job records.
- File links (`/files/<id>?token=...`) carry an unguessable per-file token, like a presigned URL.
- Uploads expire after 24 hours and results after 12 by default; `DELETE /job/{id}` removes them at once.
- Webhooks can be signed with `READANYDOC_WEBHOOK_SECRET` ([verification](../guides/jobs-webhooks.md#verifying-the-signature)).

## Where documents go

To your machine, and to the model provider you configure, under your account and that provider's data terms.
Nothing is sent to the ReadAnyDoc project. For documents that must not leave your network, use a self-hosted model
through `openai_compatible:<model>@<url>`.

## Reporting a vulnerability

Use GitHub's private "Report a vulnerability" on the
[repository](https://github.com/arpitpatel25/ReadAnyDoc/security). Please do not open a public issue.
