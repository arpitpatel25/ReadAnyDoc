"""Form grid or data table (forms/grid.py): a data table must never be converted."""
from readanydoc.pipeline.forms import grid

W2 = """<table>
<tr><td>22222</td><td>a Employee's social security number<br>128-00-XXXX</td><td>OMB No. 1545-0029</td></tr>
<tr><td>b Employer identification number (EIN)<br>25-7XXXXXX</td><td>1 Wages, tips, other compensation<br>$28,000</td><td>2 Federal income tax withheld<br>$2,800</td></tr>
<tr><td rowspan="2">c Employer's name, address, and ZIP code<br>Fashionista</td><td>3 Social security wages<br>$28,000</td><td>4 Social security tax withheld<br>$1,736</td></tr>
<tr><td>5 Medicare wages and tips</td><td>6 Medicare tax withheld</td></tr>
</table>"""
CMS = """<table><tr><td>1. MEDICARE [ ] (Medicare#)<br>MEDICAID [ ] (Medicaid#)</td><td>1a. INSURED'S I.D. NUMBER (For Program in Item 1)</td></tr>
<tr><td>2. PATIENT'S NAME (Last Name, First Name, Middle Initial)</td><td>3. PATIENT'S BIRTH DATE MM DD YY &nbsp;&nbsp; SEX M [ ] F [ ]</td><td>4. INSURED'S NAME</td></tr>
<tr><td>CITY</td><td>STATE</td><td>8. RESERVED FOR NUCC USE</td></tr></table>"""
SERVICE_LINES = """<table><tr><th>24. A. DATE(S) OF SERVICE</th><th>B. PLACE OF SERVICE</th><th>F. $ CHARGES</th><th>I. ID. QUAL.</th></tr>
<tr><td>1</td><td></td><td></td><td>NPI</td></tr><tr><td>2</td><td></td><td></td><td>NPI</td></tr><tr><td>3</td><td></td><td></td><td>NPI</td></tr></table>"""
AMOUNTS = """<table><tr><td>A. Origination Charges</td><td>$1,802</td></tr><tr><td>Application Fee</td><td>$300</td></tr>
<tr><td>Underwriting Fee</td><td>$1,097</td></tr></table>"""
NUMBERED_LINES = """<table><tr><td>1</td><td>Net income (loss) per books</td><td>48,920</td></tr>
<tr><td>2</td><td>Income included on Schedule K</td><td>1,200</td></tr><tr><td>3</td><td>Guaranteed payments</td><td></td></tr></table>"""
SPEC = """<table><tr><td>Profondeur:</td><td>50 mm</td><td>72 mm</td><td>50 mm</td><td>72 mm</td></tr>
<tr><td>Hauteurs:</td><td colspan="2">350 - 600 mm<br>700 - 2000 mm</td><td colspan="2">215 - 2115 mm</td></tr>
<tr><td>Longueurs:</td><td colspan="2">228 - 3952 mm</td><td colspan="2">500 - 2000 mm</td></tr>
<tr><td>Pression de service:</td><td>10 bars / 16 bars</td><td>10 bars / 16 bars</td><td>10 bars / 16 bars</td><td>10 bars / 16 bars</td></tr></table>"""
MARK_COLUMNS = """<table><tr><td>TSANG Chun Man<br>(曾晉文)</td><td>AMU470</td><td>[x]</td><td></td><td>[x]</td></tr>
<tr><td>YEN Chanan<br>(霍震南)</td><td>ARM750</td><td></td><td>[x]</td><td></td></tr>
<tr><td>ZHOU Xu Xiang<br>(周栩翔)</td><td>APM161</td><td>[x]</td><td></td><td>[x]</td></tr></table>"""
HEADED_GRID = """<table><tr><td>58 INSURED'S NAME</td><td>59 P. REL</td><td>60 INSURED'S UNIQUE ID</td></tr>
<tr><td>A</td><td></td><td></td></tr><tr><td>B</td><td></td><td></td></tr><tr><td>C</td><td></td><td></td></tr></table>"""
PAIRS = """<table><tr><td>PRODUCER</td><td>Insurance Agent or Broker listed here.</td></tr><tr><td>CONTACT NAME:</td><td>Jane Doe</td></tr>
<tr><td>PHONE (A/C, No, Ext):</td><td></td></tr><tr><td>E-MAIL ADDRESS:</td><td>jane@example.org</td></tr></table>"""
CC = """<table><tr><td>CC:</td><td>Vincent Losito<br>George Barody</td><td>Don Kisling<br>S. Tesh (handwritten)</td><td>C. Hill (handwritten)</td></tr></table>"""
BRIDGE = """<table><tr><td></td><td>NORTH<br>♠ J 10 8<br>♥ Q 10</td><td></td></tr><tr><td>WEST<br>♠ 4 2</td><td></td><td>EAST<br>♠ A 7</td></tr>
<tr><td></td><td>SOUTH<br>♠ K Q 9</td><td></td></tr></table>"""


def test_form_grids_are_recognised():
    for t in (W2, CMS, PAIRS):
        assert grid.classify(t).kind == "form", grid.classify(t)


def test_data_tables_are_never_form_grids():
    for t in (SERVICE_LINES, AMOUNTS, NUMBERED_LINES, SPEC, MARK_COLUMNS, HEADED_GRID, CC, BRIDGE):
        assert grid.classify(t).kind != "form", (t[:60], grid.classify(t))
    assert grid.classify(SERVICE_LINES).kind == "data"
    assert grid.classify(AMOUNTS).kind == "data"


def test_values_are_not_box_numbers():
    assert grid.field_strength(grid.Cell("50 mm")) == 0
    assert grid.field_strength(grid.Cell("S. Tesh (handwritten)")) == 0
    assert grid.field_strength(grid.Cell("1a Nonemployee compensation")) == 2
    assert grid.field_strength(grid.Cell("b Employer identification number")) == 2
    assert grid.field_strength(grid.Cell("[x]")) == 0
    assert grid.field_strength(grid.Cell("Self [ ] Spouse [ ]")) == 2


def test_to_key_value_keeps_every_cell_text_in_order():
    kv = grid.to_key_value(W2)
    assert "<td" not in kv and "<table" not in kv
    assert kv.split("\n")[:3] == ["22222", "a Employee's social security number", "128-00-XXXX"]
    import re
    words = lambda s: re.findall(r"[^\W_]+", re.sub(r"<[^>]+>", " ", s))
    assert words(kv) == words(W2.replace("&nbsp;", " "))


def test_pair_rows_become_label_value_lines():
    kv = grid.to_key_value(PAIRS)
    assert "CONTACT NAME: Jane Doe" in kv.split("\n")
    assert "PHONE (A/C, No, Ext):" in kv.split("\n")


def test_parse_expands_spans():
    t = grid.parse('<table><tr><td rowspan="2">a</td><td colspan="2">b</td></tr><tr><td>c</td><td>d</td></tr></table>')
    assert t.widths == [3, 3] and t.grid[1] == ["a", "c", "d"]
