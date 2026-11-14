from readanydoc.pipeline.checks import htmlgrid as H

OK = "<table><tr><th>a</th><th>b</th></tr><tr><td>1</td><td>2</td></tr></table>"
ROWSPAN = ("<table><tr><td rowspan=\"2\">k</td><td>1</td><td>2</td></tr>"
           "<tr><td>3</td><td>4</td></tr></table>")
BAD_ROWSPAN = ("<table><tr><td rowspan=\"2\">k</td><td>1</td><td>2</td></tr>"
               "<tr><td>x</td><td>3</td><td>4</td></tr></table>")
TITLE_ROW = ("<table><tr><td>a</td><td>b</td><td>c</td></tr><tr><td>Section</td></tr>"
             "<tr><td>1</td><td>2</td><td>3</td></tr></table>")
WIDE_EMPTY = ("<table><tr><th colspan=\"2\"></th><th colspan=\"2\">Dec 31</th></tr>"
              "<tr><td>x</td><td>1</td><td>2</td></tr><tr><td>y</td><td>3</td><td>4</td></tr></table>")
TRAILING = ("<table><tr><td>a</td><td>b</td></tr><tr><td>1</td><td>2</td><td></td></tr>"
            "<tr><td>3</td><td>4</td></tr></table>")
SHORT = ("<table><tr><td>a</td><td>b</td><td>c</td></tr><tr><td>1</td><td>2</td></tr>"
         "<tr><td>3</td><td>4</td><td>5</td></tr></table>")


def test_widths_follow_rowspan_and_colspan():
    assert H.parse(OK).widths == [2, 2] and H.consistent(OK) is True
    assert H.parse(ROWSPAN).widths == [3, 3] and H.consistent(ROWSPAN) is True
    assert H.parse(BAD_ROWSPAN).widths == [3, 4] and H.consistent(BAD_ROWSPAN) is False
    assert H.consistent("<table><tr><td>only</td></tr></table>") is None


def test_expanded_repeats_spanning_cells():
    ex = H.parse(ROWSPAN).expanded()
    assert [[c.text for c in row] for row in ex] == [["k", "1", "2"], ["k", "3", "4"]]


def test_normalise_title_row():
    new, fixes = H.normalise(TITLE_ROW)
    assert new and H.consistent(new) is True and 'colspan="3"' in new and fixes


def test_normalise_empty_spanning_cell_and_trailing_cells():
    new, _ = H.normalise(WIDE_EMPTY)
    assert new and H.consistent(new) is True and "<th></th>" in new and 'colspan="2">Dec 31' in new
    new, _ = H.normalise(TRAILING)
    assert new and H.consistent(new) is True and new.count("<td></td>") == 0


def test_normalise_leaves_ambiguous_rows_alone():
    assert H.normalise(SHORT)[0] is None
    assert H.normalise(BAD_ROWSPAN)[0] is None
    assert H.normalise(OK)[0] is None  # nothing to do


def test_pad_makes_the_browser_grid_explicit():
    new, fixes = H.normalise(SHORT, pad=True)
    assert new and H.consistent(new) is True and new.count("<td></td>") == 1
    assert H.text_bags(new) == H.text_bags(SHORT)
    new, _ = H.normalise(BAD_ROWSPAN, pad=True)
    assert new and H.consistent(new) is True


def test_replacement_must_keep_the_numbers():
    good = SHORT.replace("<td>2</td></tr>", "<td></td><td>2</td></tr>")
    assert H.acceptable(SHORT, good) == (True, "consistent grid, same cell texts")
    changed = good.replace("<td>5</td>", "<td>6</td>")
    ok, why = H.acceptable(SHORT, changed)
    assert not ok and "numbers" in why
    ok, why = H.acceptable(SHORT, SHORT)
    assert not ok and "not consistent" in why
    assert H.acceptable(SHORT, None)[0] is False


def test_replacement_must_keep_the_words_and_rows():
    draft = ("<table><tr><td>Revenue</td><td>Cost</td><td>Margin</td></tr><tr><td>North region</td><td>1</td></tr>"
             "<tr><td>South region</td><td>2</td><td>3</td></tr><tr><td>East region</td><td>4</td><td>5</td></tr><tr><td>West region</td><td>6</td><td>7</td></tr></table>")
    other = ("<table><tr><td>Alpha</td><td>Beta</td><td>Gamma</td></tr><tr><td>Delta</td><td>1</td><td></td></tr>"
             "<tr><td>Epsilon</td><td>2</td><td>3</td></tr><tr><td>Zeta</td><td>4</td><td>5</td></tr><tr><td>Eta</td><td>6</td><td>7</td></tr></table>")
    ok, why = H.acceptable(draft, other)
    assert not ok and "words" in why
    one_row = "<table><tr><td>Revenue Cost Margin North region South region East region West region</td><td>1 2 3 4 5 6 7</td></tr><tr><td></td><td></td></tr></table>"
    assert H.acceptable(draft, one_row)[0] is False


def test_thousands_separators_do_not_count_as_a_change():
    a = "<table><tr><td>x</td><td>1,234</td></tr><tr><td>y</td></tr></table>"
    b = "<table><tr><td>x</td><td>1234</td></tr><tr><td>y</td><td></td></tr></table>"
    assert H.compatible(a, b).ok


def test_a_reread_that_drops_a_word_or_flips_a_checkbox_is_rejected():
    draft = "<table><tr><td>Net revenues</td><td>10</td><td>[x] paid</td></tr><tr><td>Costs</td><td>4</td></tr></table>"
    good = "<table><tr><td>Net revenues</td><td>10</td><td>[x] paid</td></tr><tr><td>Costs</td><td>4</td><td></td></tr></table>"
    assert H.acceptable(draft, good)[0]
    assert not H.acceptable(draft, good.replace("Net revenues", "Revenues"))[0]
    assert not H.acceptable(draft, good.replace("[x]", "[ ]"))[0]
    assert H.acceptable(draft, good.replace("Net revenues", "Revenues"), words_at=0.6)[0]
