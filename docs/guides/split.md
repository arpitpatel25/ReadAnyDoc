# Split

Split finds where each document starts and ends inside one combined file, for example a loan packet with an
application, bank statements and tax returns scanned as one PDF. Split is a server feature.

```bash
curl -s -X POST http://localhost:8000/split -H "content-type: application/json" -d '{
  "input": "reducto://packet.pdf",
  "split_description": [
    {"name": "Application", "description": "The signed loan application form"},
    {"name": "Bank statement", "description": "Monthly bank statements", "partition_key": "account number"},
    {"name": "Tax return", "description": "Federal tax returns"}
  ],
  "settings": {"allow_page_overlap": true}
}'
```

With Reducto's SDK: `client.split.run(input=..., split_description=[...])`.

| Field | Meaning |
|---|---|
| `split_description[]` | `name`, `description`, optional `partition_key` to split one type further (one part per account, per year) |
| `split_rules` | free-text rules, default: sections may only overlap at their first and last page |
| `settings.allow_page_overlap` | default `true` |
| `settings.deep_split` | a slower, more careful pass |
| `settings.table_cutoff` | `truncate` or `preserve` |

The response lists each section with its pages and a confidence (`conf`). A section whose boundary was changed by a
rule rather than by evidence is reported with low confidence.
