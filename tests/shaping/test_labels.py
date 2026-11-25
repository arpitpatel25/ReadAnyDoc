"""Block-label tidying (pipeline/shaping/labels.py): Key Value kept only on form-like pages, never on native files."""
from readanydoc.model import BBox, Document, Page, Region, RegionType
from readanydoc.pipeline.shaping import labels


def _doc(kinds, file_type="pdf"):
    regions = [Region(type=k, content="x", bbox=BBox(left=0.1, top=0.1 + i * 0.05, width=0.5, height=0.03, page=0),
                      order=i) for i, k in enumerate(kinds)]
    return Document(file_type=file_type, pages=[Page(index=0, width=600, height=800, regions=regions)])


def test_few_key_values_become_text():
    d = _doc([RegionType.TEXT, RegionType.KEY_VALUE, RegionType.KEY_VALUE])
    st = labels.tidy(d, page_numbers=False)
    assert st["kv_to_text"] == 2 and all(r.type == RegionType.TEXT for r in d.pages[0].regions)


def test_form_page_keeps_key_values():
    d = _doc([RegionType.KEY_VALUE] * 3)
    labels.tidy(d, page_numbers=False)
    assert all(r.type == RegionType.KEY_VALUE for r in d.pages[0].regions)


def test_native_files_untouched():
    d = _doc([RegionType.KEY_VALUE], file_type="eml")
    labels.tidy(d)
    assert d.pages[0].regions[0].type == RegionType.KEY_VALUE


def test_page_number_pattern():
    ok = ["12", "- 7 -", "Page 3", "iv", "3 / 10", "第 5 页"]
    bad = ["Total 12 items", "2026-11-25", "12.5", "Chapter 3"]
    assert all(labels.PAGE_NUMBER.match(s) for s in ok) and not any(labels.PAGE_NUMBER.match(s) for s in bad)


def test_page_number_from_text_layer_without_ocr(monkeypatch):
    from readanydoc.model import Word
    d = _doc([RegionType.TEXT])
    p = d.pages[0]
    p.text_layer_usable = True
    p.words["pdf"] = [Word(text="Body", bbox=BBox(left=0.1, top=0.1, width=0.2, height=0.02, page=0), source="pdf", line=0),
                      Word(text="17", bbox=BBox(left=0.48, top=0.95, width=0.03, height=0.015, page=0), source="pdf", line=1)]
    monkeypatch.setattr(labels, "_page_image", lambda *a: (_ for _ in ()).throw(AssertionError("OCR path used")))
    st = labels.tidy(d)
    assert st["page_numbers_added"] == 1
    pn = [r for r in p.regions if r.type == RegionType.PAGE_NUMBER]
    assert pn[0].content == "17" and pn[0] is p.regions[-1]
