# Edit

Edit fills in PDF forms from plain-language instructions. Fillable PDFs have their own fields filled. Flat and
scanned PDFs get real form fields added where the boxes are, so the output stays fillable. Edit is a server feature.

```bash
curl -s -X POST http://localhost:8000/edit -H "content-type: application/json" -d '{
  "document_url": "reducto://w9.pdf",
  "edit_instructions": "Name: Alder & Moss LLC. Tax classification: LLC. Address: 12 Mill Lane, Austin TX.",
  "edit_options": {"color": "#000000", "flatten": false}
}'
```

The response has `document_url`: a link to the filled PDF on your server, valid for 24 hours.

| Field | Meaning |
|---|---|
| `edit_instructions` | what to fill, in plain language |
| `edit_options.color`, `font_size` | how filled text looks |
| `edit_options.flatten` | bake the values into the page |
| `edit_options.flat_fields: false` | ReadAnyDoc addition: draw text instead of adding fields on flat PDFs |
| `form_schema[]` | optional: your own boxes (`bbox`, `description`, `type`: text, checkbox, radio, dropdown, barcode) |

On a scanned W-2, ReadAnyDoc found 69 fields against Reducto's 68 and filled 24 against 23.
