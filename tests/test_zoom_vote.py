from readanydoc.pipeline.reading import zoom
from readanydoc.readers import prompts


def test_vote_needs_both_strip_reads():
    page = "<table><tr><td>Canada</td><td>5,390</td></tr><tr><td>India</td><td>856</td></tr></table>"
    a = "Canada 5,399 India 886"
    b = "Canada 5,399 India 856"
    text, changes = zoom.vote(page, a, b)
    assert changes == [("5,390", "5,399")]
    assert "<td>5,399</td>" in text and "<td>856</td>" in text  # one strip alone never changes a token


def test_vote_keeps_markup_and_unrelated_tokens():
    page = "## Total\n\n<td colspan=\"2\">1.6547</td> and [illegible] here"
    text, changes = zoom.vote(page, "Total 1.5547 and 38.7156 here", "Total 1.5547 and 38.7156 here")
    assert text == "## Total\n\n<td colspan=\"2\">1.5547</td> and 38.7156 here"
    assert len(changes) == 2


def test_vote_ignores_dissimilar_tokens():
    text, changes = zoom.vote("amount 500 paid", "amount paid", "amount 73,112 paid")
    assert changes == [] and text == "amount 500 paid"
    assert zoom.vote("name Smith", "name 1234", "name 1234")[1] == []  # a word never becomes a number


def test_disputes_and_strip_boxes():
    assert zoom.disputes("a 123 b 456", "a 123 b 466") == 1
    assert zoom.disputes("a 123 b 456", "a 123 b 456") == 0
    boxes = zoom.strip_boxes(3)
    assert boxes[0][1] == 0.0 and abs(boxes[-1][3] - 1.0) < 1e-9
    assert all(b1[3] > b2[1] for b1, b2 in zip(boxes, boxes[1:]))  # strips overlap


def test_inline_prompt_has_no_file_placeholder():
    for p in (prompts.PAGE_PROMPT_V2_FILE, prompts.STRIP_PROMPT_FILE, prompts.PAGE_PROMPT_FULL_FILE):
        assert "{img}" not in prompts.inline(p)


def test_strip_restart_keeps_the_second_attempt_only():
    from readanydoc.pipeline.reading.robust import strip_restart
    body = "1: \n2: \n3a PAT. CNTL #: \n" * 5
    bad = "<table><tr><td>1:</td></tr></table>\n\nWait, that is not how I want to present this. Here is the complete transcription:\n\n" + body
    assert strip_restart(bad) == body.strip()
    assert strip_restart(body) == body
    assert strip_restart("Dear Sir,\nWait, the shipment is late.\nRegards") == "Dear Sir,\nWait, the shipment is late.\nRegards"


def test_strip_restart_leaves_document_text_alone():
    from readanydoc.pipeline.reading.robust import strip_restart
    letter = "Dear Sir,\nWait, the shipment is late.\n" + "The goods will arrive next week.\n" * 20
    assert strip_restart(letter) == letter


def test_tangled_form_needs_checkboxes_inside_a_table():
    assert zoom.tangled_form("<table><tr><td>MEDICARE [ ]</td><td>MEDICAID [x]</td></tr></table>")
    assert not zoom.tangled_form("<table><tr><td>Revenue</td><td>1,200</td></tr></table>")
    assert not zoom.tangled_form("Married [x] Single [ ]\n\n<table><tr><td>a</td></tr></table>")


def test_insert_agreed_fills_one_empty_cell_and_restores_a_heading():
    page = ("<table><tr><td>Poland</td><td>18.8460</td><td></td><td>18.9</td></tr></table>\n\n"
            "Some intro text here.\n\nBody paragraph continues.")
    a = "Poland 18.8460 60.4396 18.9 Some intro text here. CHAPTER IV THE COMPANY Body paragraph continues."
    text, done = zoom.insert_agreed(page, a, a)
    assert "<td>60.4396</td>" in text and "\n\nCHAPTER IV THE COMPANY\n\n" in text and len(done) == 2
    assert zoom.insert_agreed(page, a, "Poland 18.8460 18.9 Some intro text here. Body paragraph continues.")[1] == []


def test_insert_agreed_restores_a_dropped_running_header_at_the_top():
    page = "# ALL MEMBER BANKS\n\n[Amounts in thousands of dollars]"
    strips = "JANUARY 1935 FEDERAL RESERVE BULLETIN 57 ALL MEMBER BANKS Amounts in thousands of dollars"
    text, done = zoom.insert_agreed(page, strips, strips)
    assert text.startswith("JANUARY 1935 FEDERAL RESERVE BULLETIN 57\n\n# ALL MEMBER BANKS") and done == [("line", "JANUARY 1935 FEDERAL RESERVE BULLETIN 57")]
    assert zoom.insert_agreed(page, strips, "ALL MEMBER BANKS Amounts in thousands of dollars")[1] == []
