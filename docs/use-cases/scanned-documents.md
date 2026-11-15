---
title: OCR for scanned documents and handwriting
description: Read scanned PDFs, faxes, old typewritten pages and handwriting into text and data with a vision model plus local OCR checks.
---

# OCR for scanned documents and handwriting

Scans are where classic OCR struggles: skew, stains, faded type, handwriting in the margins. ReadAnyDoc reads scanned pages with a vision model, checks the result against local OCR, re-reads doubtful strips zoomed in, and sends handwritten pages to a stronger model. Characters it cannot read are marked `[illegible]` instead of guessed.

```bash
pip install "readanydoc[engine]"
export ANTHROPIC_API_KEY=...
```

```python
import readanydoc

result = readanydoc.parse("scan.pdf")
print(result.markdown)
for w in result.warnings:
    print("check:", w)
```

## Tips

- See a faded 1904 invoice read end to end in [Real documents](../examples/real-documents.md).
- Handwritten pages go to Claude Sonnet 5.5 by default; `handwriting_model=None` keeps them on the main model.
- Install Tesseract (`brew install tesseract` or `apt install tesseract-ocr`) for the best results on scans.

## Next

- [See two real documents end to end](../examples/real-documents.md), with every citation drawn on the page
- [Extract guide](../guides/extract.md) and [Parse guide](../guides/parse.md)
- Already on Reducto? [Switch in one URL](../migrate/from-reducto.md)
- Questions: [GitHub Discussions](https://github.com/arpitpatel25/ReadAnyDoc/discussions)
