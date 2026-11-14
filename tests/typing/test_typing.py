"""Workstream A: deterministic block typing, segmentation, filter_blocks on the new types, estimates."""

from pathlib import Path

import pytest

from readanydoc.model import BBox, Document, Page, Region, RegionType, Word
from readanydoc.pipeline import ParseOptions
from readanydoc.pipeline.ground import Ground
from readanydoc.pipeline.grounding.sources import fix_cropped_pdf_words
from readanydoc.pipeline.mdblocks import split_markdown
from readanydoc.pipeline.shape import build_chunks
from readanydoc.pipeline.typing.layout import is_page_number, retype_page
from readanydoc.pipeline.typing.markup import classify_text, is_key_value, split_list_items
from readanydoc.serialize import to_parse_response

T = RegionType


def put(words, text, x, y, h=0.012, cw=0.008, gap=0.006):
    x0 = x
    for t in text.split():
        w = cw * len(t)
        if x + w > 0.95:  # wrap like a paragraph
            x, y = x0, y + 1.3 * h
        words.append(Word(text=t, bbox=BBox(left=x, top=y, width=w, height=h), source="pdf"))
        x += w + gap


def page_doc(items):
    """items: (markdown, x, y[, word height]) -> a one-page Document whose text layer holds every block."""
    words, regions = [], []
    for it in items:
        md, x, y = it[:3]
        b = split_markdown(md)[0]
        import re
        put(words, re.sub(r"[#*\[\]]", " ", md), x, y, h=it[3] if len(it) > 3 else 0.012)
        regions.append(Region(type=b.type, heading_level=b.heading_level, content=md))
    page = Page(index=0, original_page=1, width=600, height=800, kind="digital", text_layer_usable=True,
                words={"pdf": words}, regions=regions)
    return Document(pages=[page])


BODY = "The quick brown fox jumps over the lazy dog and keeps running through the long field until night falls"


# ---------- text rules ----------

@pytest.mark.parametrize("text,expected", [
    ("Customer service: 1.800.432.1000", T.KEY_VALUE),
    ("Name of proprietor: Stanley Price\nSocial security number (SSN): 000 00 0000", T.KEY_VALUE),
    ("Street Address 1:\nStreet Address 2:\nCity:", T.KEY_VALUE),
    ("6 Do you have another vehicle available for personal use? [ ] Yes [ ] No", T.KEY_VALUE),
    ("Tax year beginning ____________, 2004", T.KEY_VALUE),
    ("**Provider:** TEXAS DEPT OF HEALTH\n1100 WEST 49TH ST\nAustin, TX 78756", T.KEY_VALUE),
    ("Every agency shall make available to the public the following information: its rules, its opinions, "
     "its orders and every statement of policy it has adopted since the first day of the year in question", T.TEXT),
    ("Note: Components may not sum to totals because of rounding.", T.TEXT),
    ("The meeting starts at 10:30 in room 4", T.TEXT),
    ("[In millions of dollars]", T.TEXT),
    ("[Watermark: Proof as of November 1, 1999 (subject to change)]", T.TEXT),
    ("**Customer service information**", T.SECTION_HEADER),
    ("<h2>GOLD PRODUCTION</h2>", T.SECTION_HEADER),
    ("<h1>Annual report</h1>", T.TITLE),
    ("**DECLARATION.** (See Instruction E)", T.SECTION_HEADER),
    ("**Part I** Recipient Information", T.SECTION_HEADER),
    ("**Section 1. Employee Information and Attestation:** Employees must complete and sign Section 1 of Form I-9 "
     "no later than the first day of employment.", T.SECTION_HEADER),
    ("**Schedule A.—INTEREST ON GOVERNMENT OBLIGATIONS, ETC. (See Instruction G)**", T.SECTION_HEADER),
    ("**Net profit.** Subtract line 2 from line 1. If less than zero, you must use Schedule C.", T.TEXT),
    ("**IMPORTANT: VA COMPENSATION PAY IS NON-TAXABLE. THEREFORE, VA COMPENSATION PAY MAY BE THE GREATER BENEFIT.**",
     T.TEXT),
    ("**National Archives**\n**ATTN: Archival Programs**\n**P.O. Box 38757**", T.TEXT),
    ("*[Bank of America flag logo]*", T.FIGURE),
    ("*Figure: ACORD logo*", T.FIGURE),
    ('<p align="center"><em>[Seal of the U.S. Department of Homeland Security]</em></p>', T.FIGURE),
    ("[Signature: a large scribbled flourish]", T.SIGNATURE),
    ("[illegible]", T.TEXT),
    ("(b) Insert immediately following the period at the end of the first sentence", T.LIST_ITEM),
    ("a. It takes 25 minutes to drive from the airport", T.LIST_ITEM),
    ("S. H. & H. Chapman.", T.TEXT),
    ("<sup>2</sup> Amounts reflect the incentive compensation awarded.", T.TEXT),
])
def test_classify_text(text, expected):
    assert classify_text(text)[0] == expected


def test_key_value_needs_a_label_not_just_a_colon():
    assert is_key_value("Date: ____")
    assert not is_key_value("A long sentence that happens to contain a colon somewhere near its end: and then goes "
                            "on for a good while longer so that nobody would call it a form field at all")


def test_split_list_items_keeps_continuations_with_their_item():
    text = "- one\n  more of one\n- two\n  - nested\n- three"
    items = split_list_items(text)
    assert items == ["- one\n  more of one", "- two\n  - nested", "- three"]
    assert "\n".join(items) == text


# ---------- segmentation ----------

def test_split_markdown_granularity_and_text_is_unchanged():
    md = ("<div>\n\n**Account details**\n\nName: A. Smith\n\nAccount: 123\n\nPhone: 555-0100\n\n"
          "Plain paragraph of text that says something.\n\n- first\n- second\n\n</div>\n")
    blocks = split_markdown(md)
    assert [b.type for b in blocks] == [T.SECTION_HEADER, T.KEY_VALUE, T.TEXT, T.LIST_ITEM, T.LIST_ITEM]
    assert blocks[1].text == "Name: A. Smith\n\nAccount: 123\n\nPhone: 555-0100"  # one Key Value block, blank lines kept
    assert blocks[0].text.startswith("<div>") and blocks[-1].text.endswith("</div>")  # no markup-only block
    squash = lambda s: "".join(s.split())  # noqa: E731
    assert squash("".join(b.text for b in blocks)) == squash(md)
    # with the old behaviour available on request
    old = split_markdown(md, split_lists=False, merge_key_values=False)
    assert [b.type for b in old].count(T.KEY_VALUE) == 3 and [b.type for b in old].count(T.LIST_ITEM) == 1
    # joining the blocks with blank lines gives the page text back, character for character, except between list items
    assert "\n\n".join(b.text for b in old) == md.strip("\n")


def test_tables_and_headings_are_not_merged_with_wrappers():
    blocks = split_markdown("<div>\n\n<table><tr><td>a</td></tr></table>\n\n</div>\n\n<div>\n\n# Title\n\n</div>")
    assert T.TABLE in [b.type for b in blocks] and T.TITLE in [b.type for b in blocks]
    assert next(b for b in blocks if b.type == T.TABLE).text.startswith("<table>")
    assert next(b for b in blocks if b.type == T.TITLE).text == "# Title"


# ---------- position rules ----------

def furniture_doc():
    return page_doc([
        ("OMB No. 1545-0074", 0.80, 0.03),
        ("# Net Profit From Business", 0.35, 0.10, 0.02),
        (BODY, 0.08, 0.20),
        ("Ending balance: 54.11", 0.08, 0.50),
        (BODY.replace("quick", "slow"), 0.08, 0.60),
        ("Cat. No. 14374D", 0.45, 0.955),
        ("Page 55", 0.85, 0.955),
    ])


def test_header_footer_page_number_from_position():
    doc = Ground(tesseract="off").run(furniture_doc(), None)
    types = [r.type for r in doc.pages[0].regions]
    assert types == [T.HEADER, T.TITLE, T.TEXT, T.KEY_VALUE, T.TEXT, T.FOOTER, T.PAGE_NUMBER]
    r = doc.pages[0].regions[0]
    assert r.meta["type_before_layout"] == "Text" and r.meta["type_rule"] == "top_band"
    assert doc.pages[0].regions[-1].meta["page_number_in"] == "Footer"
    assert all(reg.content == it[0] for reg, it in zip(doc.pages[0].regions, [
        ("OMB No. 1545-0074",), ("# Net Profit From Business",), (BODY,), ("Ending balance: 54.11",),
        (BODY.replace("quick", "slow"),), ("Cat. No. 14374D",), ("Page 55",)]))  # typing never edits text


def test_page_numbers_can_follow_reducto_and_stay_furniture():
    doc = Ground(tesseract="off", page_numbers=False).run(furniture_doc(), None)
    assert doc.pages[0].regions[-1].type == T.FOOTER


def test_layout_types_off_and_auto_with_a_layout_detector():
    doc = Ground(tesseract="off", layout_types=False).run(furniture_doc(), None)
    assert [r.type for r in doc.pages[0].regions].count(T.TEXT) == 5
    doc = furniture_doc()
    doc.pages[0].layout_regions = [Region(type=T.TEXT, bbox=BBox(left=0.1, top=0.2, width=0.5, height=0.1))]
    doc = Ground(tesseract="off").run(doc, None)  # "auto": the detector's labels decide, position rules stay out
    assert T.HEADER not in [r.type for r in doc.pages[0].regions]
    assert T.KEY_VALUE in [r.type for r in doc.pages[0].regions]  # text rules still apply


def test_first_line_of_running_text_is_not_a_header_and_ledger_tail_is_not_a_footer():
    doc = page_doc([("PLOWEFERE. Companion in play.", 0.08, 0.050), (BODY + " and then " + BODY.replace("quick", "sly"), 0.08, 0.066),
                    (BODY.replace("quick", "slow"), 0.08, 0.50),
                    ("Big Freshet Sept 1898.", 0.08, 0.935), ("Mack Joins died July 21, 1918", 0.08, 0.955)])
    doc = Ground(tesseract="off").run(doc, None)
    assert [r.type for r in doc.pages[0].regions] == [T.TEXT] * 5


def test_title_follows_type_size_and_centred_capitals_are_headings():
    doc = page_doc([("# Official Form 101", 0.06, 0.22, 0.012), ("## Voluntary Petition for Bankruptcy", 0.06, 0.25, 0.02),
                    (BODY, 0.06, 0.30), (BODY.replace("quick", "slow"), 0.06, 0.40),
                    ("THE COMPANY NOT LIABLE", 0.36, 0.55), (BODY.replace("lazy", "idle"), 0.06, 0.60)])
    doc = Ground(tesseract="off").run(doc, None)
    regs = doc.pages[0].regions
    assert [r.type for r in regs[:2]] == [T.SECTION_HEADER, T.TITLE]
    assert regs[4].type == T.SECTION_HEADER and regs[4].meta["type_rule"] == "caps_centred"


def test_is_page_number():
    assert all(is_page_number(s) for s in ["87", "Page 2", "- 3 -", "Page 1 of 4", "**4**", "xii", "page 12"])
    assert not any(is_page_number(s) for s in ["2003 returns", "Form 1065", "Cat. No. 14374D", "Chapter 10", "", "2003"])


def test_retype_page_ignores_blocks_without_a_real_box():
    page = Page(index=0, regions=[Region(content="Page 2"),
                                  Region(content="x", bbox=BBox(left=0, top=0, width=1, height=1))])
    assert retype_page(page) == 0


# ---------- filter_blocks on the new types ----------

def test_filter_blocks_hides_position_typed_furniture_but_keeps_the_blocks():
    doc = Ground(tesseract="off").run(furniture_doc(), None)
    doc.chunks = build_chunks(doc, ParseOptions(filter_blocks=["Header", "Footer", "Page Number"]))
    resp = to_parse_response(doc)
    content = resp["result"]["chunks"][0]["content"]
    assert "OMB No." not in content and "Cat. No." not in content and "Page 55" not in content
    assert "Net Profit From Business" in content and "Ending balance: 54.11" in content
    types = [b["type"] for b in resp["result"]["chunks"][0]["blocks"]]
    assert {"Header", "Footer", "Page Number", "Key Value", "Title", "Text"} <= set(types)
    # a single type can be filtered on its own
    doc.chunks = build_chunks(doc, ParseOptions(filter_blocks=["Key Value"]))
    content = to_parse_response(doc)["result"]["chunks"][0]["content"]
    assert "Ending balance" not in content and "OMB No." in content and "Page 55" in content
    # nothing filtered: every character of every block is in the content
    doc.chunks = build_chunks(doc, ParseOptions())
    content = to_parse_response(doc)["result"]["chunks"][0]["content"]
    assert all(r.content in content for r in doc.pages[0].regions)


# ---------- boxes without evidence are estimates, and say so ----------

def test_unlocated_blocks_get_marked_estimates_from_neighbours():
    words = []
    put(words, "Alpha beta gamma delta", 0.1, 0.30)
    put(words, "Epsilon zeta eta theta", 0.1, 0.60)
    regions = [Region(content="Unseen heading words"), Region(content="Alpha beta gamma delta"),
               Region(content="Nothing like this exists"), Region(content="Epsilon zeta eta theta"),
               Region(content="Trailing phantom line")]
    page = Page(index=0, width=600, height=800, kind="digital", text_layer_usable=True, words={"pdf": words},
                regions=regions)
    doc = Ground(tesseract="off").run(Document(pages=[page]), None)
    blocks = to_parse_response(doc)["result"]["chunks"][0]["blocks"]
    assert [b["extra"]["bbox_source"] for b in blocks] == [
        "estimate:above_next", "ground", "estimate:between_neighbours", "ground", "estimate:below_previous"]
    assert blocks[0]["bbox"]["top"] == 0 and abs(blocks[0]["bbox"]["height"] - 0.30) < 0.01
    assert 0.30 < blocks[2]["bbox"]["top"] < blocks[2]["bbox"]["top"] + blocks[2]["bbox"]["height"] <= 0.61
    assert all(b["confidence"] == "low" for b in (blocks[0], blocks[2], blocks[4]))
    assert all(r.bbox is None for r in (regions[0], regions[2], regions[4]))  # an estimate is never stored as a box


def test_repeated_text_is_placed_between_its_reading_order_neighbours():
    """A title printed again in the footer: each block takes the occurrence between its neighbours."""
    words = []
    put(words, "Voluntary Petition for Individuals", 0.1, 0.10)
    put(words, BODY, 0.1, 0.20)
    put(words, BODY.replace("quick", "slow"), 0.1, 0.50)
    put(words, "Voluntary Petition for Individuals", 0.3, 0.90)
    regions = [Region(content="## Voluntary Petition for Individuals", type=T.SECTION_HEADER), Region(content=BODY),
               Region(content=BODY.replace("quick", "slow")), Region(content="Voluntary Petition for Individuals")]
    page = Page(index=0, width=600, height=800, kind="digital", text_layer_usable=True, words={"pdf": words},
                regions=regions)
    Ground(tesseract="off", layout_types=False).run(Document(pages=[page]), None)
    assert regions[0].bbox.top < 0.15 and regions[3].bbox.top > 0.85


# ---------- text layer of a cropped page ----------

def test_cropped_page_words_move_into_the_visible_page():
    pdf = Path(__file__).resolve().parents[3] / "bizdocs" / "pages" / "tax_irs_p334_2003_p055.pdf"
    if not pdf.exists():
        pytest.skip("public test page not present")
    from readanydoc.pipeline.triage import pdf_words
    words, _ = pdf_words(str(pdf), 1, 0)
    page = Page(index=0, original_page=1, width=612, height=792, words={"pdf": words})
    doc = Document(source=str(pdf), file_type="pdf", pages=[page], meta={"path": str(pdf)})
    before = next(w for w in words if w.text == "SCHEDULE").bbox.top
    assert fix_cropped_pdf_words(doc, page) is True
    after = next(w for w in page.words["pdf"] if w.text == "SCHEDULE").bbox.top
    assert before > 0.2 and 0.03 < after < 0.07  # the media box is 1008 pt tall, the visible page 792 pt
    assert page.properties["pdf_words_crop"]["dropped"] > 0  # words printed outside the visible page are gone
    assert fix_cropped_pdf_words(doc, page) is False  # once per page
