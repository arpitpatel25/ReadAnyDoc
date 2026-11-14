from readanydoc.pipeline.checks import numbers as N

T1935 = ("<table><tr><th>Item</th><th>Amount</th></tr>"
         "<tr><td>Member banks</td><td>12,522,980</td></tr>"
         "<tr><td>Nonmember banks</td><td>9,136,684</td></tr>"
         "<tr><td>Other</td><td>5,515,175</td></tr>"
         "<tr><td>Total</td><td>27,174,839</td></tr></table>")


def test_number_parsing():
    assert N.number("1,234.50") == (1234.5, 2)
    assert N.number("(1,234)") == (-1234.0, 0)
    assert N.number("$ 33,672") == (33672.0, 0)
    assert N.number("12 Jan") is None and N.number("") is None


def test_column_total_reconciles():
    tt = N.table_totals(T1935)
    assert len(tt) == 1 and tt[0].ok and tt[0].kind == "column"


def test_misread_digit_is_a_near_miss():
    bad = T1935.replace("9,136,684", "9,186,684")
    tt = N.table_totals(bad)
    assert len(tt) == 1 and not tt[0].ok and tt[0].near
    far = T1935.replace("9,136,684", "1,234")
    assert not N.table_totals(far)[0].near


def test_repair_accepts_only_the_change_that_reconciles():
    bad = T1935.replace("9,136,684", "9,186,684")
    new, changes, left = N.repair(bad, T1935)
    assert new == T1935 and len(changes) == 1 and changes[0]["old"] == "9,186,684" and not left


def test_repair_ignores_other_differences_of_the_reread():
    bad = T1935.replace("9,136,684", "9,186,684")
    crop = T1935.replace("12,522,980", "12,522,930")  # the re-read fixes one cell and misreads another
    new, changes, left = N.repair(bad, crop)
    assert new == T1935 and len(changes) == 1 and not left  # only the cell that makes the sum right is taken
    crop2 = T1935.replace("Member banks", "Member bnks")  # labels differ a little: that row is not matched, others are
    new, changes, left = N.repair(bad, crop2)
    assert new == T1935 and len(changes) == 1


def test_repair_does_nothing_when_the_reread_agrees_with_the_draft():
    bad = T1935.replace("9,136,684", "9,186,684")
    new, changes, left = N.repair(bad, bad)
    assert new == bad and not changes and len(left) == 1


def test_repair_aligns_a_partial_crop_by_row_labels():
    bad = T1935.replace("5,515,175", "5,575,175")
    crop = ("<table><tr><td>Nonmember banks</td><td>9,136,684</td></tr><tr><td>Other</td><td>5,515,175</td></tr>"
            "<tr><td>Total</td><td>27,174,839</td></tr></table>")
    new, changes, left = N.repair(bad, crop)
    assert new == T1935 and not left


def test_row_totals():
    t = ("<table><tr><th>Region</th><th>Q1</th><th>Q2</th><th>Total</th></tr>"
         "<tr><td>North</td><td>10</td><td>20</td><td>30</td></tr>"
         "<tr><td>South</td><td>5</td><td>6</td><td>13</td></tr></table>")
    tt = N.table_totals(t)
    assert [(x.kind, x.ok) for x in tt] == [("row", True), ("row", False)]


def test_a_year_in_the_header_is_not_a_misread():
    t = ("<table><tr><th>Item</th><th>2003</th></tr><tr><td>Fees</td><td>40,000</td></tr>"
         "<tr><td>Rent</td><td>26,563</td></tr><tr><td>Total</td><td>66,563</td></tr></table>")
    tt = N.table_totals(t)
    assert len(tt) == 1 and not tt[0].ok and tt[0].drop_one and not tt[0].near
