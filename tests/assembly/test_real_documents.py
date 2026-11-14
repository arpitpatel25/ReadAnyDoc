"""Assembly rules found by comparing real multi-page documents with Reducto r-1's responses (workstream D).
Each test rebuilds the block pattern of the real document it came from."""
from readanydoc.model import BBox, Document, Page, Region, RegionType
from readanydoc.pipeline import PipelineConfig, PipelineContext
from readanydoc.pipeline.assemble import Assemble
from readanydoc.pipeline.assembly import headings
from readanydoc.pipeline.assembly import tables as TJ

X, T, S, TI, H, F, K, L = (RegionType.TEXT, RegionType.TABLE, RegionType.SECTION_HEADER, RegionType.TITLE,
                           RegionType.HEADER, RegionType.FOOTER, RegionType.KEY_VALUE, RegionType.LIST_ITEM)
BODY = "The Postal Service recognises revenue when the service is rendered to the customer in full."


def doc_of(pages):
    """pages: list of blocks (type, content, top, bottom[, meta])."""
    doc = Document(source="x", file_type="pdf")
    for i, blocks in enumerate(pages):
        page = Page(index=i, original_page=i + 1)
        for k, b in enumerate(blocks):
            t, content, top, bottom = b[:4]
            page.regions.append(Region(type=t, content=content, order=k, meta=dict(b[4]) if len(b) > 4 else {},
                                       bbox=BBox(left=0.1, top=top, width=0.8, height=bottom - top, page=i)))
        doc.pages.append(page)
    return doc


def run(doc, **opts):
    return Assemble(enabled=True, **opts).run(doc, PipelineContext(PipelineConfig()))


def table(rows, head=("Classification", "This Month", "Prior Month")):
    return "<table><tr>" + "".join(f"<th>{h}</th>" for h in head) + "</tr>" + "".join(
        "<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows) + "</table>"


ROWS = [["Defense", "1,200", "1,100"], ["Energy", "300", "280"], ["Labor", "90", "85"]]
BAND = {"type_before_layout": "Text", "type_rule": "top_band"}


def types(doc, page):
    return [r.type for r in doc.pages[page].regions]


def test_repeated_table_caption_and_unit_line_stay_content():
    # Monthly Treasury Statement: every page of Table 5 starts with the caption and "[$ millions]"; per-page
    # typing calls both Header. Reducto: Title and Text on every page.
    pages = [[(TI, "# Table 5. Outlays of the U.S. Government, September 2024", 0.014, 0.025),
              (H, "[$ millions]", 0.028, 0.037, BAND), (T, table(ROWS), 0.09, 0.88), (X, "11", 0.975, 0.984)]]
    for n in range(12, 16):
        pages.append([(H, "Table 5. Outlays of the U.S. Government, September 2024—Continued", 0.014, 0.025, BAND),
                      (H, "[$ millions]", 0.028, 0.037, BAND), (T, table(ROWS), 0.09, 0.88),
                      (X, str(n), 0.975, 0.984)])
    doc = run(doc_of(pages))
    for p in range(1, 5):
        assert types(doc, p)[:3] == [TI, X, T]
        assert doc.pages[p].regions[0].meta["furniture_kept"] == "repeated caption"
    assert types(doc, 4)[3] == RegionType.PAGE_NUMBER  # the page numbers still are furniture
    # ... and the table still continues over the caption: same number, header repeated
    links = [l for l in doc.links if l.kind == "table_continuation"]
    assert len(links) == 4 and all(l.confidence.label == "high" for l in links)


def test_new_numbered_table_on_the_next_page_is_not_a_continuation():
    # Fed H.8: Table 2 ends a page, Table 3 with the same columns starts the next (Reducto merges them)
    pages = [[(S, "## Table 2. Assets and Liabilities of Commercial Banks", 0.11, 0.13), (T, table(ROWS), 0.2, 0.85)],
             [(S, "## Table 3. Assets and Liabilities of Commercial Banks", 0.11, 0.13), (T, table(ROWS), 0.2, 0.85)],
             [(S, "## Table 3. Assets and Liabilities of Commercial Banks (continued)", 0.11, 0.13),
              (X, "Not seasonally adjusted, billions of dollars (continued)", 0.134, 0.147), (T, table(ROWS), 0.2, 0.5),
              (X, "Footnotes appear on the last page.", 0.52, 0.54)]]
    doc = run(doc_of(pages), merge_tables=True)
    links = [l for l in doc.links if l.kind == "table_continuation"]
    assert len(links) == 1 and links[0].to_region == doc.pages[2].regions[2].id
    assert "merged_content" not in doc.pages[0].regions[1].meta
    assert doc.pages[1].regions[1].meta["merged_content"].count("<tr>") == 7  # repeated header dropped


def test_text_between_tables_that_is_no_caption_gives_no_accepted_join():
    pages = [[(T, table(ROWS), 0.1, 0.8), (X, "A closing paragraph about the table above.", 0.82, 0.86)],
             [(X, "Results for the second quarter", 0.1, 0.12), (T, table(ROWS), 0.2, 0.8)], [(X, BODY, 0.1, 0.2)]]
    doc = run(doc_of(pages))
    assert not [l for l in doc.links if l.kind == "table_continuation" and l.confidence.label == "high"]


def test_section_heading_repeated_while_the_section_continues_is_a_heading():
    # Berkshire annual report: running header, then "(16) Unpaid losses ..." again on every page of note 16
    head = "Notes to Consolidated Financial Statements"
    pages = [[(X, head, 0.056, 0.065), (S, "**(15) Dividend restrictions**", 0.079, 0.09), (X, BODY, 0.1, 0.3),
              (S, "**(16) Unpaid losses and loss adjustment expenses**", 0.31, 0.32), (X, BODY, 0.33, 0.9),
              (X, "K-92", 0.953, 0.963)]]
    for n, kind in ((93, H), (94, L), (95, S), (96, S)):
        pages.append([(X, head, 0.056, 0.065),
                      (kind, "(16) Unpaid losses and loss adjustment expenses", 0.079, 0.09, BAND if kind == H else {}),
                      (X, BODY + f" {n}", 0.1, 0.9), (X, f"K-{n}", 0.953, 0.963)])
    doc = run(doc_of(pages))
    for p in range(1, 5):
        assert types(doc, p) == [H, S, X, F]
    assert types(doc, 0)[0] == H and types(doc, 0)[3] == S


def test_running_head_below_the_fixed_band_and_on_alternate_pages():
    # Supreme Court slip opinion: text starts at 15% of the page; the head alternates between two lines
    pages = []
    for n in range(1, 13):
        head = (X, f"{n}\nLOPER BRIGHT ENTERPRISES v. RAIMONDO", 0.146, 0.158) if n % 2 == 0 else \
            (K, f"Cite as: 603 U. S. ____ (2024) {n}", 0.146, 0.158)
        if n in (10, 12):
            head = (X, f"{n}\nLOPER BRIGHT ENTERPRISES v. RAIMONDO\nOpinion of the Court", 0.146, 0.187)
        pages.append([head, (X, BODY + f" Part {n} of the argument.", 0.2, 0.5), (X, BODY, 0.52, 0.8)])
    doc = run(doc_of(pages))
    assert all(types(doc, p) == [H, X, X] for p in range(1, 12))
    assert doc.pages[9].regions[0].meta["furniture"]["extended"] is True


def test_distinct_chapter_headings_at_the_same_place_are_not_a_running_head():
    pages = [[(TI, f"# CHAPTER {r}", 0.1, 0.12), (X, BODY + f" {r}", 0.15, 0.9)] for r in ("IV", "V", "VI")]
    pages += [[(X, BODY + f" more {i}", 0.1, 0.9)] for i in range(4)]
    doc = run(doc_of(pages))
    assert [types(doc, p)[0] for p in range(3)] == [TI, TI, TI]


def test_footnote_repeated_under_the_tables_is_not_a_footer():
    pages = [[(X, BODY + f" {i}", 0.1, 0.3), (T, table(ROWS), 0.32, 0.8),
              (L, "* Unaudited required supplemental information", 0.82, 0.84), (X, f"K-{90 + i}", 0.953, 0.963)]
             for i in range(5)]
    doc = run(doc_of(pages))
    assert all(types(doc, p)[2:] == [L, F] for p in range(5))


def test_sentence_running_over_the_page_break_is_not_a_header():
    # IRS Publication 334, three columns: the first lines of a page are the end of a sentence
    cont = "electronic funds withdrawal from your checking or savings account or by credit card."
    pages = [[(X, BODY, 0.1, 0.9)], [(H, cont, 0.042, 0.069, BAND), (X, BODY, 0.1, 0.9)],
             [(H, "• Manufacturers taxes on the sale or use of a variety of different articles.", 0.042, 0.069,
               {"type_before_layout": "List Item", "type_rule": "top_band"}),
              (H, "Department of the Treasury", 0.042, 0.06, BAND), (X, BODY, 0.1, 0.9)]]
    doc = run(doc_of(pages))
    assert types(doc, 1)[0] == X and types(doc, 2)[:2] == [L, H]


def heads(pages):
    doc = doc_of(pages)
    hs = headings.apply(doc)
    return doc, hs


def test_one_page_does_not_have_three_titles():
    # a scanned tax form: "# Schedule A", "# Schedule B", ... on one page are sections (Reducto: Section Header)
    doc, _ = heads([[(TI, "# INDIVIDUAL INCOME TAX RETURN", 0.05, 0.08), (X, BODY, 0.1, 0.9)],
                    [(TI, f"# Schedule {c}.—EXPLANATION", 0.1 + 0.2 * i, 0.12 + 0.2 * i) for i, c in enumerate("ABCD")]])
    assert types(doc, 0)[0] == TI and set(types(doc, 1)) == {S}
    assert all(r.heading_level == 2 and r.meta["heading_level_source"] == "document" for r in doc.pages[1].regions)


def test_numbered_title_follows_the_numbered_section_headers():
    # Fed H.4.1: "## 1. Factors ..." is a section on page 1 and came back as the title of page 2
    doc, _ = heads([[(TI, "# FEDERAL RESERVE statistical release", 0.05, 0.08), (S, "## 1. Factors Affecting Reserve Balances", 0.2, 0.22)],
                    [(TI, "# 2. Maturity Distribution of Securities", 0.1, 0.12), (S, "## 3. Supplemental Information", 0.5, 0.52)],
                    [(S, "## 4. Information on Principal Accounts", 0.1, 0.12)]])
    assert types(doc, 1) == [S, S] and types(doc, 0)[0] == TI
    assert doc.pages[1].regions[0].meta["type_before_assemble"] == "Title"


def test_levels_have_no_gaps_and_title_is_level_one():
    hs = [headings.Heading(region=Region(type=t, content=c), page=Page(index=0), title=c, md_level=m, size=z)
          for t, c, m, z in ((TI, "Filing and Paying Business Taxes", 1, 21.0), (TI, "Income Tax", 1, 17.0),
                             (S, "Introduction", 2, 17.0), (S, "How Do I File?", 2, 14.0), (S, "Useful Items", 5, 12.0))]
    headings.unify_types(hs)
    headings.assign_levels(hs)
    headings.finalise_levels(hs)
    assert [h.region.type for h in hs] == [TI, S, S, S, S]
    assert [h.level for h in hs] == [1, 2, 2, 3, 4]
