# Classify

Classify labels a document by type, with the text that supports the label. Classify is a server feature.

```bash
curl -s -X POST http://localhost:8000/classify -H "content-type: application/json" -d '{
  "input": "reducto://doc.pdf",
  "classification_schema": [
    {"category": "Invoice", "criteria": ["has an invoice number", "has a total due"]},
    {"category": "Receipt", "criteria": ["shows a payment already made"]},
    {"category": "Contract", "criteria": ["has parties and signatures"]}
  ],
  "model": "accurate"
}'
```

With Reducto's SDK: `client.classify.run(input=..., classification_schema=[...])`.

| Field | Meaning |
|---|---|
| `classification_schema[]` | `category` and its `criteria` |
| `category_groups` | optional grouping of categories |
| `model` | `default`, or `accurate`: the quoted evidence is checked against the page text in the same call |
| `page_range` | classify from some pages only |
| `document_metadata` | extra context, for example where the file came from |
