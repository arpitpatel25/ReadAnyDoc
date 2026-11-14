# Security

Please report vulnerabilities privately through GitHub's "Report a vulnerability" (Security tab) rather than in a
public issue. Include the version, the configuration involved and steps to reproduce. You will get an answer within
a week.

## What to know when running the server

- Set `READANYDOC_API_KEY` before the port is reachable by anyone else; without it the server is open.
- URL inputs that resolve to private or loopback addresses are refused by default (`READANYDOC_BLOCK_PRIVATE_URLS`).
  Turn this off only on a server nobody else can call.
- Local file paths as input are refused unless `READANYDOC_ALLOW_LOCAL_PATHS=1`. Leave it off on a shared server.
- Documents are sent to the model provider you configure (Anthropic or OpenAI by default) under your account and
  that provider's data terms. For documents that must not leave your network, point the reader at a self-hosted
  OpenAI-compatible server.
- Uploads and results are kept on disk for `READANYDOC_UPLOAD_TTL_HOURS` / `READANYDOC_RESULT_TTL_HOURS` and then
  removed; `DELETE /job/{id}` removes them at once.
- Provider API keys are read from the environment and are never written to logs, results or job records.
