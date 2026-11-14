"""Reading quality: evidence, candidate selection, text fusion, tables, escalation.

Everything here is deterministic and model-free. The stages in ``triage.py``, ``read.py`` and
``verify.py`` call into these modules; each mechanism has its own stage option and ablation row.
The rule throughout: keep the model's output, change it only where
independent evidence (PDF text layer, OCR words, a second read) agrees, never rewrite with a model.
"""
