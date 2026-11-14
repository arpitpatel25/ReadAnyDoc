import csv
import io
import json

import pytest

from readanydoc.pipeline.shaping import tables as T

MERGED = ('<table><thead><tr><th rowspan="2">A</th><th colspan="2">B &amp; C</th></tr><tr><th>b</th><th>c</th></tr>'
          '</thead><tr><td>1</td><td>x|y<br>z</td><td></td></tr><tr><td>2</td><td colspan=2>w, "q"</td></tr></table>')
SIMPLE = "<table><tr><th>Name</th><th>Qty</th></tr><tr><td>a</td><td>1</td></tr><tr><td>b</td><td>2</td></tr></table>"


def test_parse_grid_with_spans():
    g = T.parse_table(MERGED)
    assert (g.n_rows, g.n_cols, g.cell_count, g.merged_count, g.header_rows) == (4, 3, 9, 3, 2)
    assert g.matrix() == [["A", "B & C", "B & C"], ["A", "b", "c"], ["1", "x|y\nz", ""], ["2", 'w, "q"', 'w, "q"']]
    assert g.matrix(fill_merged=False)[1] == ["", "b", "c"]


def test_html_is_passed_through_untouched():
    assert T.convert_table(MERGED, "html") == (MERGED, "html")


def test_md_flattens_and_escapes():
    md, fmt = T.convert_table(MERGED, "md")
    assert fmt == "md"
    lines = md.split("\n")
    assert lines[0] == "| A | B & C | B & C |" and lines[1] == "| --- | --- | --- |"
    assert "x\\|y<br>z" in lines[3]
    back = T.parse_table(md)
    assert back.matrix() == T.parse_table(MERGED).matrix()


def test_csv_and_json_round_trip():
    g = T.parse_table(MERGED)
    rows = list(csv.reader(io.StringIO(T.convert_table(MERGED, "csv")[0])))
    assert rows == g.matrix()
    assert json.loads(T.convert_table(MERGED, "json")[0]) == g.matrix()


def test_md_to_html():
    md = T.to_md(T.parse_table(SIMPLE))
    out, fmt = T.convert_table(md, "html")
    assert fmt == "html" and T.parse_table(out).matrix() == T.parse_table(SIMPLE).matrix()
    assert T.convert_table(md, "md") == (md, "md")


@pytest.mark.parametrize("cells,merged,want", [(30, 0, "md"), (32, 0, "html"), (20, 1, "html"), (20, 4, "html")])
def test_dynamic_rule(cells, merged, want):
    """Stricter than Reducto's stated rule: any rowspan/colspan keeps the HTML."""
    rows = []
    plain = ["<td>x%d</td>" % i for i in range(cells - merged)]
    for i in range(0, len(plain), 2):
        rows.append("<tr>" + "".join(plain[i:i + 2]) + "</tr>")
    rows += ['<tr><td colspan="2">m</td></tr>'] * merged
    html = "<table>" + "".join(rows) + "</table>"
    g = T.parse_table(html)
    assert g.cell_count == cells and g.merged_count == merged
    assert T.dynamic_format(g) == want
    out, fmt = T.convert_table(html, "dynamic")
    assert fmt == want and (out == html if want == "html" else out.startswith("| x0 | x1 |"))


def test_dynamic_never_duplicates_merged_cells_but_explicit_formats_still_flatten():
    out, fmt = T.convert_table(MERGED, "dynamic")
    assert (out, fmt) == (MERGED, "html")
    rowspan = '<table><tr><th>K</th><th>V</th></tr><tr><td rowspan="2">a</td><td>1</td></tr><tr><td>2</td></tr></table>'
    assert T.convert_table(rowspan, "dynamic") == (rowspan, "html")
    assert T.convert_table(rowspan, "md")[0].count("| a |") == 2  # asked for md: flattened as before
    assert T.convert_table(MERGED, "csv")[1] == "csv" and T.convert_table(MERGED, "json")[1] == "json"


def test_dynamic_requires_a_lossless_round_trip():
    ragged = "<table><tr><td>a</td><td>b</td></tr><tr><td>c</td></tr></table>"
    assert T.convert_table(ragged, "dynamic")[1] == "html"  # markdown would invent a cell
    caption = "<table><caption>Sales</caption><tr><td>a</td><td>b</td></tr><tr><td>c</td><td>d</td></tr></table>"
    assert T.convert_table(caption, "dynamic")[1] == "html"
    one_row = "<table><tr><td>a</td><td>b</td></tr></table>"
    assert T.convert_table(one_row, "dynamic")[1] == "html"  # a markdown table needs a header and a body
    tricky = "<table><tr><th>A|B</th><th>C</th></tr><tr><td>x<br>y</td><td>back\\slash</td></tr></table>"
    g = T.parse_table(tricky)
    assert T.md_is_lossless(g) == (T.parse_md_table(T.to_md(g)).matrix() == g.matrix())
    out, fmt = T.convert_table(SIMPLE, "dynamic")
    assert fmt == "md" and T.parse_table(out).matrix() == T.parse_table(SIMPLE).matrix()


def test_dynamic_keeps_html_when_cells_carry_attributes():
    html = '<table><tr><td data-formula="=A1">1</td><td>2</td></tr></table>'
    assert T.convert_table(html, "dynamic")[1] == "html"


def test_jsonbbox_needs_boxes():
    assert T.convert_table(SIMPLE, "jsonbbox")[1] == "json"
    box = {"left": 0.1, "top": 0.1, "width": 0.2, "height": 0.05, "page": 1}
    out, fmt = T.convert_table(SIMPLE, "jsonbbox", {(0, 0): box})
    data = json.loads(out)
    assert fmt == "jsonbbox" and data[0][0] == {"text": "Name", "bbox": box} and data[1][1]["bbox"] is None


def test_text_around_the_table_is_kept_and_non_tables_untouched():
    out, _ = T.convert_table("Table 1: sales\n" + SIMPLE + "\nSource: x", "md")
    assert out.startswith("Table 1: sales\n\n| Name | Qty |") and out.endswith("Source: x")
    assert T.convert_table("just text", "md") == ("just text", "unchanged")


def test_nested_table_and_broken_markup_do_not_crash():
    g = T.parse_table("<table><tr><td>a<table><tr><td>in</td><td>ner</td></tr></table></td><td>b</td></tr></table>")
    assert g.n_cols == 2 and "in" in g.cells[0].text
    assert T.parse_table("<table><tr><td>a") .matrix() == [["a"]]
    assert T.parse_table("<table></table>") is None
