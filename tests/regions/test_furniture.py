"""Round 3, workstream A: free structure rules (regions/furniture.py). No model call, no network."""

from readanydoc.model import BBox, Line, Page, Region, RegionType, TableCell, Word
from readanydoc.pipeline.regions import Switches, furniture


def det(i, label, type_, box, score=0.9):
    return {"id": f"d{i}", "label": label, "type": type_, "score": score, "box": list(box), "order": i, "inside": None}


def bbox(box):
    return BBox(left=box[0], top=box[1], width=box[2] - box[0], height=box[3] - box[1])


def region(type_, content, box=None, words=()):
    """``words``: (text, left, right, top) in text order; char offsets are found in the content."""
    r = Region(type=type_, content=content, text=content, bbox=bbox(box) if box else None)
    pos, ws = 0, []
    for t, x0, x1, y in words:
        k = content.index(t, pos)
        ws.append(Word(text=t, bbox=bbox((x0, y, x1, y + 0.01)), char_start=k, char_end=k + len(t)))
        pos = k + len(t)
    if ws:
        r.lines = [Line(words=ws)]
    return r


def page_with(regions, dets=(), words=()):
    p = Page(index=0, width=612, height=792)
    p.regions = list(regions)
    p.properties["layout_detections"] = list(dets)
    if words:
        p.words["pdf"] = [Word(text=t, bbox=bbox((x0, y, x1, y + 0.01))) for t, x0, x1, y in words]
    return p


def test_round3_switches_parse_and_are_off_by_default():
    assert not Switches.parse("types=guarded,boxes,split,hfsplit,kvsplit").more.keys() & set(furniture.SWITCHES)
    s = Switches.parse("hfdet,pnsplit,hftable,prose,lab=cap+list")
    assert s.more == {"hfdet": "on", "pnsplit": "on", "hftable": "on", "prose": "on", "lab": "cap+list"} and s.any()


# ------------------------------------------------------------------------------------- cut at furniture detections
def test_heading_line_over_two_page_header_detections_is_cut():
    c = "# H.8 ASSETS AND LIABILITIES Page 2"
    r = region(RegionType.TITLE, c, (0.07, 0.08, 0.93, 0.10),
               [("H", 0.07, 0.08, 0.085), ("8", 0.085, 0.09, 0.085), ("ASSETS", 0.10, 0.2, 0.085), ("AND", 0.21, 0.25, 0.085),
                ("LIABILITIES", 0.26, 0.4, 0.085), ("Page", 0.89, 0.92, 0.085), ("2", 0.925, 0.93, 0.085)])
    dets = [det(1, "page_header", "Header", (0.07, 0.08, 0.4, 0.1)), det(2, "page_header", "Header", (0.89, 0.08, 0.93, 0.1))]
    p = page_with([r], dets)
    assert furniture.split_by_detections(p, dets) == 1
    assert [x.content for x in p.regions] == ["# H.8 ASSETS AND LIABILITIES", "Page 2"]
    assert [x.type for x in p.regions] == [RegionType.TITLE, RegionType.TEXT]     # the page number is no heading
    assert p.regions[0].candidates[-1].content == c                             # the block before the cut is kept


def test_wrapped_line_with_entities_is_cut_and_tokens_left_out_by_grounding_are_found_on_the_page():
    c = "<p>JANUARY 1935 &nbsp;&nbsp; FEDERAL RESERVE BULLETIN &nbsp;&nbsp; 19</p>"
    r = region(RegionType.HEADER, c, (0.36, 0.05, 0.63, 0.06),
               [("FEDERAL", 0.36, 0.44, 0.05), ("RESERVE", 0.45, 0.53, 0.05), ("BULLETIN", 0.54, 0.63, 0.05)])
    dets = [det(1, "page_header", "Header", (0.08, 0.05, 0.16, 0.06)), det(2, "page_header", "Header", (0.36, 0.05, 0.63, 0.06)),
            det(3, "page_header", "Header", (0.9, 0.045, 0.92, 0.06))]
    words = [("JANUARY", 0.08, 0.12, 0.05), ("1935", 0.13, 0.16, 0.05), ("FEDERAL", 0.36, 0.44, 0.05), ("RESERVE", 0.45, 0.53, 0.05),
             ("BULLETIN", 0.54, 0.63, 0.05), ("19", 0.9, 0.92, 0.05), ("19", 0.5, 0.52, 0.4)]
    p = page_with([r], dets, words)
    assert furniture.split_by_detections(p, dets) == 1
    assert [x.content for x in p.regions] == ["<p>JANUARY 1935</p>", "<p>FEDERAL RESERVE BULLETIN</p>", "<p>19</p>"]


def test_footer_lines_under_each_other_and_text_outside_detections_are_not_cut():
    c = "Digitized for FRASER\nFederal Reserve Bank of St. Louis"
    stacked = region(RegionType.FOOTER, c, (0.0, 0.96, 0.23, 1.0),
                     [("Digitized", 0.0, 0.06, 0.965), ("for", 0.07, 0.09, 0.965), ("FRASER", 0.1, 0.17, 0.965),
                      ("Federal", 0.0, 0.05, 0.985), ("Reserve", 0.06, 0.11, 0.985), ("Bank", 0.12, 0.15, 0.985),
                      ("of", 0.16, 0.17, 0.985), ("St", 0.18, 0.19, 0.985), ("Louis", 0.2, 0.23, 0.985)])
    body = region(RegionType.TEXT, "Total amount due", (0.1, 0.5, 0.9, 0.51),
                  [("Total", 0.1, 0.2, 0.5), ("amount", 0.5, 0.6, 0.5), ("due", 0.8, 0.9, 0.5)])
    dets = [det(1, "page_footer", "Footer", (0.0, 0.964, 0.177, 0.977)), det(2, "page_footer", "Footer", (0.0, 0.984, 0.233, 0.999))]
    p = page_with([stacked, body], dets)
    assert furniture.split_by_detections(p, dets) == 0 and len(p.regions) == 2


# ------------------------------------------------------------------------------------------------- page numbers
def test_page_number_standing_clear_at_the_end_of_a_footer_is_cut_off():
    c = "**Voluntary Petition for Individuals Filing for Bankruptcy** page 1"
    r = region(RegionType.FOOTER, c, (0.33, 0.95, 0.67, 0.96),
               [("Voluntary", 0.33, 0.4, 0.95), ("Petition", 0.41, 0.46, 0.95), ("for", 0.47, 0.49, 0.95),
                ("Individuals", 0.5, 0.56, 0.95), ("Filing", 0.57, 0.6, 0.95), ("for", 0.61, 0.62, 0.95), ("Bankruptcy", 0.6, 0.67, 0.95)])
    words = [("page", 0.845, 0.87, 0.95), ("1", 0.875, 0.883, 0.95), ("1", 0.2, 0.21, 0.3)]
    p = page_with([r], [], words)
    assert furniture.split_page_numbers(p, []) == 1
    assert [x.content for x in p.regions] == ["**Voluntary Petition for Individuals Filing for Bankruptcy**", "page 1"]
    assert all(x.type == RegionType.FOOTER and x.meta["split_rule"] == "pnsplit" for x in p.regions)


def test_bare_number_needs_a_wide_gap_and_a_year_or_a_form_number_is_never_cut():
    def head(text, gap_at):
        return region(RegionType.HEADER, text, (0.2, 0.05, 0.8, 0.06),
                      [("MIRANDA", 0.2, 0.3, 0.05), ("ARIZONA", 0.32, 0.42, 0.05), (text.split()[-1], gap_at, gap_at + 0.03, 0.05)])
    far, close, year = head("MIRANDA ARIZONA 485", 0.67), head("MIRANDA ARIZONA 485", 0.44), head("MIRANDA ARIZONA 1999", 0.67)
    form = region(RegionType.FOOTER, "Form 1065", (0.1, 0.95, 0.3, 0.96), [("Form", 0.1, 0.15, 0.95), ("1065", 0.25, 0.3, 0.95)])
    body = region(RegionType.TEXT, "see page 2", (0.1, 0.5, 0.5, 0.51), [("see", 0.1, 0.13, 0.5), ("page", 0.3, 0.34, 0.5), ("2", 0.35, 0.36, 0.5)])
    p = page_with([far, close, year, form, body])
    assert furniture.split_page_numbers(p, []) == 1
    assert [x.content for x in p.regions] == ["MIRANDA ARIZONA", "485", "MIRANDA ARIZONA 485", "MIRANDA ARIZONA 1999", "Form 1065", "see page 2"]


def test_leading_page_number_is_cut_off():
    r = region(RegionType.HEADER, "436 MIRANDA *v.* ARIZONA.", (0.07, 0.05, 0.6, 0.06),
               [("436", 0.07, 0.1, 0.05), ("MIRANDA", 0.38, 0.46, 0.05), ("v", 0.47, 0.48, 0.05), ("ARIZONA", 0.49, 0.58, 0.05)])
    p = page_with([r])
    assert furniture.split_page_numbers(p, []) == 1
    assert [x.content for x in p.regions] == ["436", "MIRANDA *v.* ARIZONA."]


# ----------------------------------------------------------------------------------------------- tables that are not
def cells(r, boxes):
    r.cells = [TableCell(row=0, col=i, text=f"c{i}", bbox=bbox(b) if b else None) for i, b in enumerate(boxes)]
    return r


def test_small_table_on_a_page_header_detection_becomes_one_block_per_cell():
    t = cells(region(RegionType.TABLE, "<table>\n<tr><td>Form 1065 (1999)</td><td>Page <b>4</b></td></tr>\n</table>", (0.11, 0.065, 0.2, 0.077)),
              [(0.11, 0.066, 0.2, 0.076), None])                               # the second cell was not located
    data = region(RegionType.TABLE, "<table><tr><td>1</td><td>2</td></tr><tr><td>3</td><td>4</td></tr></table>", (0.1, 0.4, 0.9, 0.5))
    fields = cells(region(RegionType.TABLE, "<table><tr><td>DATE</td><td>08/13/2020</td></tr></table>", (0.03, 0.955, 0.2, 0.968)),
                   [(0.03, 0.958, 0.07, 0.968), (0.09, 0.955, 0.16, 0.965)])   # a value outside the footer detection
    dets = [det(1, "page_header", "Header", (0.11, 0.065, 0.2, 0.077)), det(2, "page_header", "Header", (0.88, 0.065, 0.93, 0.077)),
            det(3, "page_footer", "Footer", (0.03, 0.957, 0.075, 0.969))]
    p = page_with([t, data, fields], dets)
    assert furniture.furniture_tables(p, dets) == 1
    assert [x.content for x in p.regions[:2]] == ["Form 1065 (1999)", "Page **4**"]
    assert [x.type for x in p.regions] == [RegionType.TEXT, RegionType.TEXT, RegionType.TABLE, RegionType.TABLE]


def test_small_table_where_the_layout_sees_a_table_stays():
    t = cells(region(RegionType.TABLE, "<table><tr><td>Form 1065</td><td>Page 4</td></tr></table>", (0.11, 0.065, 0.93, 0.077)),
              [(0.11, 0.066, 0.2, 0.076), (0.88, 0.066, 0.93, 0.076)])
    dets = [det(1, "page_header", "Header", (0.11, 0.065, 0.2, 0.077)), det(2, "page_header", "Header", (0.88, 0.065, 0.93, 0.077))]
    p = page_with([t], dets + [det(3, "table", "Table", (0.1, 0.06, 0.94, 0.08))])
    assert furniture.furniture_tables(p, p.properties["layout_detections"]) == 0
    assert furniture.furniture_tables(p, dets) == 1


PROSE = ("<table>\n<tr>\n<td>REDLINE COMPARISON</td>\n</tr>\n<tr>\n<td>§ 335.10.30 Limitations on Use<br><br>Contracting without "
         "providing for full and open competition must be approved in writing as set forth in § 335.30 by the purchasing "
         "office's chief judge. The approval is filed with the contract.<br>[. . .]<br>(b) orders placed against single-award "
         "national judiciary contracts or blanket purchase agreements <b>are exempt</b>;</td>\n</tr>\n</table>")


def test_one_column_table_of_running_text_becomes_text_blocks():
    t = region(RegionType.TABLE, PROSE, (0.06, 0.03, 0.94, 0.86))
    p = page_with([t], [det(1, "text", "Text", (0.07, 0.2, 0.9, 0.3))])
    assert furniture.unwrap_prose(p, p.properties["layout_detections"]) == 1
    assert [x.content[:28] for x in p.regions] == ["REDLINE COMPARISON", "§ 335.10.30 Limitations on U", "Contracting without providin",
                                                   "[. . .]", "(b) orders placed against si"]
    assert p.regions[-1].type == RegionType.LIST_ITEM and p.regions[2].type == RegionType.TEXT
    assert p.regions[-1].content.endswith("**are exempt**;")                    # inline tags as the reader's marks
    assert "".join(x.text for x in p.regions).count("Contracting") == 1
    assert p.regions[0].candidates[-1].content == PROSE                         # the table as read is kept


def test_real_tables_form_boxes_and_tables_the_layout_sees_are_not_unwrapped():
    two_cols = region(RegionType.TABLE, PROSE.replace("<td>REDLINE COMPARISON</td>", "<td>REDLINE</td><td>COMPARISON</td>"), (0.06, 0.03, 0.94, 0.86))
    short = region(RegionType.TABLE, "<table><tr><td>DATE (MM/DD/YYYY)<br>01/01/2025</td></tr></table>", (0.8, 0.05, 0.95, 0.08))
    box = region(RegionType.TABLE, PROSE.replace("[. . .]", "[ ] 1 U.S. Government Plaintiff"), (0.06, 0.03, 0.94, 0.86))
    seen = region(RegionType.TABLE, PROSE, (0.06, 0.03, 0.94, 0.86))
    for r, dets in ((two_cols, []), (short, []), (box, []), (seen, [det(1, "table", "Table", (0.06, 0.03, 0.94, 0.86))])):
        p = page_with([r], dets)
        assert furniture.unwrap_prose(p, dets) == 0 and p.regions == [r]


# -------------------------------------------------------------------------------------------------- label rules
def matched(r, d):
    r.meta["layout_detection"] = {"id": d["id"], "label": d["label"], "how": "match", "iou": 0.9}
    return r


def test_label_rules_follow_the_layout_only_on_a_match():
    cap_d, head_d = det(1, "caption", "Text", (0.1, 0.3, 0.6, 0.32)), det(2, "section_header", "Section Header", (0.1, 0.4, 0.3, 0.42))
    cap = matched(region(RegionType.SECTION_HEADER, "**Figure 2. Outlays by month**", (0.1, 0.3, 0.6, 0.32)), cap_d)
    marked = matched(region(RegionType.SECTION_HEADER, "## Figure 2. Outlays by month", (0.1, 0.3, 0.6, 0.32)), cap_d)
    item = matched(region(RegionType.LIST_ITEM, "(3) Liquidity risk", (0.1, 0.4, 0.3, 0.42)), head_d)
    sentence = matched(region(RegionType.LIST_ITEM, "(3) The bank holds liquid assets.", (0.1, 0.4, 0.3, 0.42)), head_d)
    free = region(RegionType.LIST_ITEM, "(4) Credit risk", (0.1, 0.5, 0.3, 0.52))
    p = page_with([cap, marked, item, sentence, free], [cap_d, head_d])
    assert furniture.label_rules(p, [cap_d, head_d], "cap+list") == {"cap": 1, "list": 1}
    assert [r.type for r in p.regions] == [RegionType.TEXT, RegionType.SECTION_HEADER, RegionType.SECTION_HEADER,
                                           RegionType.LIST_ITEM, RegionType.LIST_ITEM]
    assert cap.meta["type_before_round3"] == "Section Header"


def test_paragraph_typed_section_header_and_bracketed_unit_line_are_text():
    para = region(RegionType.SECTION_HEADER, "**Regulators and society.** " + "The agencies must balance fast technical change. " * 8, (0.1, 0.2, 0.9, 0.3))
    head = region(RegionType.SECTION_HEADER, "**Regulators and society**", (0.1, 0.4, 0.9, 0.42))
    unit = region(RegionType.HEADER, "<p>[In millions of dollars]</p>", (0.3, 0.09, 0.7, 0.1))
    unit.meta.update({"type_before_layout": "Text", "type_rule": "top_band_2"})
    running = region(RegionType.HEADER, "FEDERAL RESERVE BULLETIN", (0.3, 0.05, 0.7, 0.06))
    running.meta.update({"type_before_layout": "Text", "type_rule": "top_band"})
    p = page_with([para, head, unit, running])
    assert furniture.label_rules(p, [], "long+paren") == {"long": 1, "paren": 1}
    assert [r.type for r in p.regions] == [RegionType.TEXT, RegionType.SECTION_HEADER, RegionType.TEXT, RegionType.HEADER]


def test_labels_read_out_of_a_picture_become_figure_but_a_framed_part_of_the_page_does_not():
    pic = det(1, "picture", "Figure", (0.3, 0.5, 0.7, 0.8))
    labels = [region(RegionType.TEXT, t, (0.35 + 0.1 * i, 0.6, 0.42 + 0.1 * i, 0.62)) for i, t in enumerate(["470kΩ", "HCP 1.6m", "ground plane"])]
    p = page_with(labels, [pic])
    assert furniture.label_rules(p, [pic], "fig") == {"fig": 3} and all(r.type == RegionType.FIGURE for r in p.regions)
    framed = [region(RegionType.SECTION_HEADER, "Notes", (0.35, 0.52, 0.5, 0.54))] + \
             [region(RegionType.TEXT, t, (0.35, 0.6 + 0.05 * i, 0.65, 0.62 + 0.05 * i)) for i, t in enumerate(["a", "b", "c"])]
    p2 = page_with(framed, [pic])
    assert furniture.label_rules(p2, [pic], "fig") == {} and framed[1].type == RegionType.TEXT


def test_profile_engine_final_3_adds_only_the_round3_switches():
    from readanydoc.profiles import get_profile
    two, three = get_profile("engine-final-2"), get_profile("engine-final-3")
    r2, r3 = two.stage("ground").options["regions"], three.stage("ground").options["regions"]
    assert r3 == r2 + ",hfdet,pnsplit,hftable,prose,lab=paren"
    assert {k: v for k, v in three.stage("ground").options.items() if k != "regions"} == \
        {k: v for k, v in two.stage("ground").options.items() if k != "regions"}
    assert three.stage("read").options == two.stage("read").options and three.reader == two.reader
