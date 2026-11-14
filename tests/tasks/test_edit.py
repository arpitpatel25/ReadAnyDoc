"""Edit: fillable PDF (generated with pikepdf in the fixture), flat PDF with a form_schema, DOCX."""

import json
import re

import pdfplumber
import pikepdf
import pytest
from jsonschema import Draft202012Validator

from readanydoc import paths
from readanydoc.tasks import FakeLLM, TaskError, edit

VALUES = {"full name": "Priya Sharma (QA)", "birth": "04/12/1990", "agree": True, "plan": "Premium",
          "country": "united states", "comments": "Please call before delivery. Leave the parcel with the neighbour."}


def mapper(values=VALUES):
    """A fake model that reads the field list from the prompt and answers by description keyword."""
    def handler(system, user, i):
        fields = [json.loads(line) for line in user.split("Fields:\n")[1].split("\n\nReturn")[0].splitlines()]
        out = []
        for f in fields:
            for key, val in values.items():
                if key in f["description"].lower():
                    out.append({"id": f["id"], "value": val})
                    break
        return {"fields": out}
    return handler


def field_values(path):
    """Read a saved PDF back: {field name: (V, [AS of each widget])}."""
    out = {}
    with pikepdf.open(path) as pdf:
        for a in pdf.pages[0].obj.get("/Annots") or []:
            fld = a if "/T" in a else a["/Parent"]
            v = fld.get("/V")
            entry = out.setdefault(str(fld["/T"]), [None if v is None else str(v), []])
            if "/AS" in a:
                entry[1].append(str(a["/AS"]))
    return out


def test_widgets_are_enumerated_with_types_boxes_and_labels(form_pdf, tmp_path):
    llm = FakeLLM([{"fields": []}])
    out = edit.run(None, {"edit_instructions": "nothing to fill"}, llm=llm, source_path=form_pdf,
                   output_path=tmp_path / "out.pdf")
    schema = out["form_schema"]
    assert [w["type"] for w in schema] == ["text", "text", "text", "checkbox", "radio", "radio", "dropdown"]
    assert [w["description"] for w in schema] == [
        "Full name (field: full_name)", "Date of birth (MM/DD/YYYY) (field: dob)", "Comments",
        "I agree to the terms (field: agree)", "Basic (field: plan)", "Premium (field: plan)", "Country"]
    for w in schema:
        assert set(w) == {"bbox", "description", "type", "fill", "value", "font_size", "filled"}
        b = w["bbox"]
        assert b["page"] == 1 and 0 <= b["left"] <= 1 and 0 <= b["top"] <= 1 and 0 < b["width"] <= 1 and 0 < b["height"] <= 1
    # /Rect [160 700 400 718] on a 612 x 792 page, top-left origin
    assert schema[0]["bbox"] == {"left": 0.2614, "top": 0.0934, "width": 0.3922, "height": 0.0227, "page": 1, "original_page": 1}
    assert {k: out["usage"][k] for k in ("num_pages", "credits")} == {"num_pages": 1, "credits": None}
    assert out["usage"]["model_usage"]["calls"] == 1 and out["response_type"] == "edit"
    # the model sees one entry per field (the radio group is one field with options)
    shown = [json.loads(line) for line in llm.calls[0]["user"].split("Fields:\n")[1].split("\n\nReturn")[0].splitlines()]
    assert [f["type"] for f in shown] == ["text", "text", "text", "checkbox", "radio", "dropdown"]
    assert shown[4]["options"] == ["Basic", "Premium"] and shown[5]["options"] == ["India", "Germany", "United States"]
    spec_path = paths.reference_dir() / "openapi.json"
    if spec_path.exists():
        spec = json.loads(spec_path.read_text())
        Draft202012Validator({"$ref": "#/components/schemas/EditResponse", "components": spec["components"]}).validate(
            {**out, "job_id": "j"})


def test_fill_text_checkbox_radio_dropdown_and_read_back(form_pdf, tmp_path):
    out = edit.run(None, {"edit_instructions": "Fill in Priya's details."}, llm=FakeLLM(mapper()),
                   source_path=form_pdf, output_path=tmp_path / "filled.pdf")
    assert out["document_url"] == str(tmp_path / "filled.pdf")
    got = field_values(out["document_url"])
    assert got["full_name"][0] == "Priya Sharma (QA)"
    assert got["dob"][0] == "04/12/1990"
    assert got["comments"][0].startswith("Please call before delivery.")
    assert got["agree"] == ["/Yes", ["/Yes"]]
    assert got["plan"] == ["/Premium", ["/Off", "/Premium"]]
    assert got["country"][0] == "US"  # the export value of the option shown as "United States"
    with pikepdf.open(out["document_url"]) as pdf:
        assert pdf.Root.AcroForm.NeedAppearances == True  # noqa: E712
        ap = pdf.pages[0].Annots[0].AP.N.read_bytes()
        assert b"(Priya Sharma \\(QA\\)) Tj" in ap
    assert field_values(form_pdf)["full_name"][0] is None  # the source file is untouched


def test_dropdown_and_radio_values_that_are_not_options_are_skipped_silently(form_pdf, tmp_path):
    values = {"country": "Atlantis", "plan": "Platinum", "full name": "Ada"}
    out = edit.run(None, {"edit_instructions": "x"}, llm=FakeLLM(mapper(values)), source_path=form_pdf,
                   output_path=tmp_path / "o.pdf")
    got = field_values(out["document_url"])
    assert got["country"][0] is None
    assert got["plan"] == ["/Off", ["/Off", "/Off"]]
    assert got["full_name"][0] == "Ada"  # the rest is still filled


def test_checkbox_can_be_unticked_and_unknown_ids_are_ignored(form_pdf, tmp_path):
    first = edit.run(None, {"edit_instructions": "x"}, llm=FakeLLM(mapper()), source_path=form_pdf,
                     output_path=tmp_path / "a.pdf")
    llm = FakeLLM([{"fields": [{"id": 3, "value": False}, {"id": 99, "value": "x"}, {"id": "0", "value": "y"}]}])
    second = edit.run(None, {"edit_instructions": "untick"}, llm=llm, source_path=first["document_url"],
                      output_path=tmp_path / "b.pdf")
    got = field_values(second["document_url"])
    assert got["agree"] == ["/Off", ["/Off"]]
    assert got["full_name"][0] == "Priya Sharma (QA)"  # untouched
    shown = llm.calls[0]["user"]
    assert '"current": "Priya Sharma (QA)"' in shown and '"current": "Premium"' in shown


def test_flatten_turns_fields_into_page_content(form_pdf, tmp_path):
    out = edit.run(None, {"edit_instructions": "x", "edit_options": {"flatten": True}}, llm=FakeLLM(mapper()),
                   source_path=form_pdf, output_path=tmp_path / "flat.pdf")
    with pikepdf.open(out["document_url"]) as pdf:
        assert "/AcroForm" not in pdf.Root
        assert not [a for a in (pdf.pages[0].obj.get("/Annots") or []) if a.get("/Subtype") == "/Widget"]
    with pdfplumber.open(out["document_url"]) as doc:
        text = doc.pages[0].extract_text()
    assert "Full name: Priya Sharma (QA)" in text and "Date of birth: 04/12/1990" in text
    assert "Country: United States" in text and "Please call before delivery." in text
    assert len(out["form_schema"]) == 7


def test_font_size_global_and_per_field(form_pdf, tmp_path):
    out = edit.run(None, {"edit_instructions": "x", "edit_options": {"font_size": 14}}, llm=FakeLLM(mapper()),
                   source_path=form_pdf, output_path=tmp_path / "a.pdf")
    with pikepdf.open(out["document_url"]) as pdf:
        assert str(pdf.pages[0].Annots[0].DA) == "/Helv 14 Tf 0 g"
        assert b"/Helv 14 Tf" in pdf.pages[0].Annots[0].AP.N.read_bytes()
    schema = out["form_schema"]
    schema[0].update(value="Ada Lovelace", font_size=7.5)
    schema[1].update(value="12/10/1815")
    out = edit.run(None, {"edit_instructions": "", "edit_options": {"font_size": 14}, "form_schema": schema},
                   llm=FakeLLM(), source_path=form_pdf, output_path=tmp_path / "b.pdf")
    with pikepdf.open(out["document_url"]) as pdf:
        assert str(pdf.pages[0].Annots[0].DA) == "/Helv 7.5 Tf 0 g"   # the field's own size wins
        assert str(pdf.pages[0].Annots[1].DA) == "/Helv 14 Tf 0 g"    # falls back to the global size
    for bad in (0, 100, "12"):
        with pytest.raises(TaskError) as e:
            edit.run(None, {"edit_instructions": "x", "edit_options": {"font_size": bad}}, llm=FakeLLM(),
                     source_path=form_pdf, output_path=tmp_path / "c.pdf")
        assert e.value.status_code == 400


def test_incoming_form_schema_fill_value_semantics_and_overlap_rule(form_pdf, tmp_path):
    base = edit.run(None, {"edit_instructions": ""}, llm=FakeLLM(), source_path=form_pdf,
                    output_path=tmp_path / "base.pdf")["form_schema"]
    schema = json.loads(json.dumps(base))
    schema[0]["value"] = "Grace Hopper"                 # value given: used directly, no model needed
    schema[1]["fill"] = False                           # never filled, never shown to the model
    schema[2]["description"] = "Delivery notes"        # edited description is what the model sees
    schema[3]["bbox"]["left"] += schema[3]["bbox"]["width"] * 0.4    # 60% overlap: still the checkbox
    schema[5]["value"] = "true"                         # select the "Premium" radio button
    schema[6]["bbox"]["left"] += schema[6]["bbox"]["width"] * 0.6    # 40% overlap: no longer the dropdown
    schema[6]["value"] = "Germany"
    llm = FakeLLM(mapper({"delivery notes": "Ring twice", "agree": True, "birth": "01/01/2000", "basic": True}))
    out = edit.run(None, {"edit_instructions": "Add the delivery note and tick the box.", "form_schema": schema},
                   llm=llm, source_path=form_pdf, output_path=tmp_path / "out.pdf")
    got = field_values(out["document_url"])
    assert got["full_name"][0] == "Grace Hopper"
    assert got["dob"][0] is None
    assert got["comments"][0] == "Ring twice"
    assert got["agree"] == ["/Yes", ["/Yes"]]
    assert got["plan"] == ["/Premium", ["/Off", "/Premium"]]
    assert got["country"][0] is None                    # below 50% overlap: the widget is not touched...
    assert got["temp_0"][0] == "Germany"                # ...a new field at the given box holds the value instead
    shown = llm.calls[0]["user"]
    assert "Delivery notes" in shown and "Date of birth" not in shown and "Grace" not in shown
    assert out["form_schema"][0]["value"] == "Grace Hopper" and out["form_schema"][1]["fill"] is False
    assert len(out["form_schema"]) == 7


def test_form_schema_with_all_values_needs_no_model_call(form_pdf, tmp_path):
    base = edit.run(None, {"edit_instructions": ""}, llm=FakeLLM(), source_path=form_pdf,
                    output_path=tmp_path / "base.pdf")["form_schema"]
    for w, v in zip(base, ["A", "B", "C", "yes", "no", "yes", "India"]):
        w["value"] = v
    llm = FakeLLM()
    out = edit.run(None, {"edit_instructions": "ignored", "form_schema": base}, llm=llm, source_path=form_pdf,
                   output_path=tmp_path / "out.pdf")
    assert llm.calls == []  # deterministic: no detection, no mapping
    got = field_values(out["document_url"])
    assert got["agree"][0] == "/Yes" and got["plan"][0] == "/Premium" and got["country"][0] == "India"


def test_flat_pdf_draws_text_and_checks_at_schema_boxes(flat_pdf, tmp_path):
    schema = [{"bbox": {"left": 0.25, "top": 0.2, "width": 0.4, "height": 0.025, "page": 1},
               "description": "Full name", "type": "text", "value": "Zoë O'Neil (x)"},
              {"bbox": {"left": 0.25, "top": 0.3, "width": 0.4, "height": 0.025, "page": 1},
               "description": "City", "type": "text"},
              {"bbox": {"left": 0.1, "top": 0.4, "width": 0.02, "height": 0.015, "page": 1},
               "description": "Consent given", "type": "checkbox"}]
    llm = FakeLLM(mapper({"city": "Pune", "consent": True}))
    out = edit.run(None, {"edit_instructions": "City is Pune; consent given.", "form_schema": schema,
                          "edit_options": {"flat_fields": False}}, llm=llm,
                   source_path=flat_pdf, output_path=tmp_path / "out.pdf")
    with pdfplumber.open(out["document_url"]) as doc:
        page = doc.pages[0]
        text = page.extract_text()
        words = {w["text"]: w for w in page.extract_words()}
        n_lines = len(page.lines) + len(page.curves)
    assert "Full name: ______________" in text            # original content kept
    assert "Zoë O'Neil (x)" in text and "Pune" in text
    w = words["Pune"]                                      # drawn inside its box (25%-65% wide, 30%-32.5% down)
    assert 0.25 * 612 <= w["x0"] <= 0.3 * 612 and 0.3 * 792 <= (w["top"] + w["bottom"]) / 2 <= 0.325 * 792
    assert n_lines >= 2                                    # the check mark is two strokes
    assert out["form_schema"][1]["description"] == "City"


FLAT_SCHEMA = [{"bbox": {"left": 0.25, "top": 0.2, "width": 0.4, "height": 0.025, "page": 1},
                "description": "Full name", "type": "text", "value": "Zoë O'Neil (x)"},
               {"bbox": {"left": 0.25, "top": 0.3, "width": 0.4, "height": 0.025, "page": 1},
                "description": "City", "type": "text"},
               {"bbox": {"left": 0.1, "top": 0.4, "width": 0.02, "height": 0.015, "page": 1},
                "description": "Consent given", "type": "checkbox"},
               {"bbox": {"left": 0.1, "top": 0.45, "width": 0.02, "height": 0.015, "page": 1},
                "description": "Newsletter", "type": "checkbox"},
               {"bbox": {"left": 0.25, "top": 0.5, "width": 0.5, "height": 0.1, "page": 1},
                "description": "Notes", "type": "text"},
               {"bbox": {"left": 0.25, "top": 0.7, "width": 0.3, "height": 0.03, "page": 1},
                "description": "Barcode", "type": "barcode"}]


def _red_pixels(path, box, forms=True):
    """Count red pixels inside a normalised box of page 1, rendered by pdfium (with form fields drawn)."""
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(str(path))
    try:
        if forms:
            pdf.init_forms()
        im = pdf[0].render(scale=2, may_draw_forms=forms).to_pil().convert("RGB")
    finally:
        pdf.close()
    w, h = im.size
    crop = im.crop((int(box["left"] * w), int(box["top"] * h), int((box["left"] + box["width"]) * w),
                    int((box["top"] + box["height"]) * h)))
    return sum(1 for r, g, b in crop.getdata() if r > 180 and g < 90 and b < 90)


def test_flat_pdf_gets_real_form_fields_at_the_schema_boxes(flat_pdf, tmp_path):
    llm = FakeLLM(mapper({"city": "Pune", "consent": True}))
    out = edit.run(None, {"edit_instructions": "City is Pune; consent given.", "form_schema": FLAT_SCHEMA}, llm=llm,
                   source_path=flat_pdf, output_path=tmp_path / "out.pdf")
    assert [(w["value"], w["filled"]) for w in out["form_schema"]] == [
        ("Zoë O'Neil (x)", True), ("Pune", True), ("True", True), (None, False), (None, False), (None, False)]
    with pikepdf.open(out["document_url"]) as pdf:
        fields = list(pdf.Root.AcroForm.Fields)
        annots = list(pdf.pages[0].Annots)
        assert len(fields) == len(annots) == 5              # every box except the barcode, filled or not
        by_name = {str(f.T): f for f in fields}
        assert sorted(by_name) == ["temp_0", "temp_1", "temp_2", "temp_3", "temp_4"]   # Reducto's naming
        assert [str(by_name[f"temp_{i}"].FT) for i in range(5)] == ["/Tx", "/Tx", "/Btn", "/Btn", "/Tx"]
        assert str(by_name["temp_0"].V) == "Zoë O'Neil (x)" and str(by_name["temp_1"].V) == "Pune"
        assert str(by_name["temp_2"].V) == "/Yes" and str(by_name["temp_2"].AS) == "/Yes"
        assert str(by_name["temp_3"].V) == "/Off" and str(by_name["temp_3"].AS) == "/Off"   # stays tickable
        assert "/V" not in by_name["temp_4"] and int(by_name["temp_4"].Ff) == 4096          # empty, multi-line
        assert sorted(str(k) for k in by_name["temp_2"].AP.N.keys()) == ["/Off", "/Yes"]
        assert str(by_name["temp_1"].TU) == "City" and int(by_name["temp_1"].F) == 4
        assert "1.000 0.000 0.000 rg" in str(by_name["temp_1"].DA)       # edit_options.color, red by default
        assert b"(Pune) Tj" in by_name["temp_1"].AP.N.read_bytes()
        x0, y0, x1, y1 = (float(v) for v in by_name["temp_1"].Rect)       # the box, in PDF points from the bottom
        assert (round(x0), round(x1)) == (153, 398) and (round(y1), round(y0)) == (554, 535)
        assert "/Helv" in pdf.Root.AcroForm.DR.Font and "/NeedAppearances" not in pdf.Root.AcroForm
    with pdfplumber.open(out["document_url"]) as doc:
        text = doc.pages[0].extract_text()
    assert "Full name: ______________" in text and "Pune" not in text    # the page content itself is untouched
    assert _red_pixels(out["document_url"], FLAT_SCHEMA[1]["bbox"]) > 20    # a viewer shows the value in its box
    assert _red_pixels(out["document_url"], FLAT_SCHEMA[2]["bbox"]) > 5     # and the tick
    assert _red_pixels(out["document_url"], FLAT_SCHEMA[3]["bbox"]) == 0


def test_flat_pdf_fields_can_be_filled_again_by_a_later_edit(flat_pdf, tmp_path):
    first = edit.run(None, {"edit_instructions": "x", "form_schema": FLAT_SCHEMA}, llm=FakeLLM(mapper({"city": "Pune"})),
                     source_path=flat_pdf, output_path=tmp_path / "one.pdf")
    # the output is now a fillable PDF: no schema needed, the fields are found in the file
    llm = FakeLLM(mapper({"notes": "Call before delivery", "newsletter": True}))
    out = edit.run(None, {"edit_instructions": "Add the note, subscribe."}, llm=llm, source_path=first["document_url"],
                   output_path=tmp_path / "two.pdf")
    got = field_values(out["document_url"])
    assert got["temp_4"][0] == "Call before delivery" and got["temp_3"][0] == "/Yes"
    assert got["temp_1"][0] == "Pune" and got["temp_0"][0] == "Zoë O'Neil (x)"
    assert "Notes" in llm.calls[0]["user"] and "Newsletter" in llm.calls[0]["user"]   # tooltips describe the fields


def test_flat_pdf_flatten_bakes_the_values_into_the_page(flat_pdf, tmp_path):
    llm = FakeLLM(mapper({"city": "Pune", "consent": True}))
    out = edit.run(None, {"edit_instructions": "x", "form_schema": FLAT_SCHEMA, "edit_options": {"flatten": True}},
                   llm=llm, source_path=flat_pdf, output_path=tmp_path / "flattened.pdf")
    with pikepdf.open(out["document_url"]) as pdf:
        assert "/AcroForm" not in pdf.Root and not pdf.pages[0].get("/Annots")
    with pdfplumber.open(out["document_url"]) as doc:
        page = doc.pages[0]
        text = page.extract_text()
        w = {x["text"]: x for x in page.extract_words()}["Pune"]
    assert "Zoë O'Neil (x)" in text and "Full name: ______________" in text
    assert 0.25 * 612 <= w["x0"] <= 0.3 * 612 and 0.3 * 792 <= (w["top"] + w["bottom"]) / 2 <= 0.325 * 792
    assert _red_pixels(out["document_url"], FLAT_SCHEMA[2]["bbox"], forms=False) > 5   # the tick is page content now
    assert [w["filled"] for w in out["form_schema"]] == [True, True, True, False, False, False]


def test_flat_field_text_is_fitted_and_colour_and_size_are_honoured(flat_pdf, tmp_path):
    schema = [{"bbox": {"left": 0.25, "top": 0.3, "width": 0.2, "height": 0.02, "page": 1}, "description": "Address",
               "type": "text", "value": "2100 Harbor Boulevard, Suite 300, Oakland"},
              {"bbox": {"left": 0.25, "top": 0.4, "width": 0.4, "height": 0.03, "page": 1}, "description": "Ref",
               "type": "text", "value": "A-1", "font_size": 14}]
    out = edit.run(None, {"edit_instructions": "x", "form_schema": schema, "edit_options": {"color": "#0000FF"}},
                   llm=FakeLLM(), source_path=flat_pdf, output_path=tmp_path / "out.pdf")
    with pikepdf.open(out["document_url"]) as pdf:
        a, b = list(pdf.Root.AcroForm.Fields)
        size = float(re.search(r"/Helv ([\d.]+) Tf", str(a.DA)).group(1))
        assert 5.5 <= size < 12 * 0.65 + 2 and "0.000 0.000 1.000 rg" in str(a.DA)
        assert "/Helv 14 Tf" in str(b.DA) and b"/Helv 14 Tf" in b.AP.N.read_bytes()


def form_image(size=(612, 792)):
    """A synthetic flat form: a ruled cell with its label at the top, and a checkbox with a label."""
    import io
    from PIL import Image, ImageDraw
    img = Image.new("L", size, 255)
    d = ImageDraw.Draw(img)
    d.rectangle((60, 100, 360, 160), outline=0, width=2)      # cell
    d.rectangle((66, 104, 200, 116), fill=0)                  # the printed label, as a block of ink
    d.rectangle((60, 300, 76, 316), outline=0, width=2)       # checkbox
    d.rectangle((84, 302, 180, 314), fill=0)                  # its label
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return img, buf.getvalue()


def test_refine_box_moves_the_text_off_the_label_and_the_rules():
    from readanydoc.tasks.detect import _largest_empty_rect, refine_box
    assert _largest_empty_rect([[True, False, False], [False, False, False], [True, False, False]]) == (1, 0, 3, 3)
    img, _ = form_image()
    l, t, r, b = refine_box(img, (55 / 612, 95 / 792, 365 / 612, 165 / 792), "text")
    assert 60 / 612 <= l and r <= 360 / 612                    # inside the rules
    assert t >= 116 / 792 and b <= 160 / 792                   # below the label, above the bottom rule
    assert (b - t) * 792 >= 30                                 # and it kept the blank part of the cell
    cl, ct, cr, cb = refine_box(img, (58 / 612, 298 / 792, 78 / 612, 318 / 792), "checkbox")
    assert 60 / 612 <= cl and cr <= 76 / 612 and 300 / 792 <= ct and cb <= 316 / 792   # the inside of the square
    blank = (0.7, 0.7, 0.9, 0.75)                              # nothing printed there: unchanged up to the inset
    got = refine_box(img, blank, "text")
    assert all(abs(x - y) < 0.01 for x, y in zip(got, blank))


def test_flat_pdf_without_schema_detects_fields_on_the_page_image_then_fills(flat_pdf, tmp_path):
    _, png = form_image()
    detected = {"fields": [
        {"description": "1 Full name", "type": "text", "bbox": [90, 120, 596, 208]},      # whole cell, label included
        {"description": "1 Full name", "type": "text", "bbox": [91, 121, 596, 208]},      # listed twice: dropped
        {"description": "2 Consent given", "type": "checkbox", "bbox": [95, 376, 127, 401]},
        {"description": "bad box", "type": "text", "bbox": [500, 500, 400, 600]}]}

    def handler(system, user, i):
        return detected if i == 0 else mapper({"full name": "Ada Lovelace", "consent": True})(system, user, i)

    llm = FakeLLM(handler)
    out = edit.run(None, {"edit_instructions": "Name Ada Lovelace, consent given."}, llm=llm, source_path=flat_pdf,
                   output_path=tmp_path / "out.pdf", options={"render": lambda path, page: png, "flat_fields": False})
    assert len(llm.calls) == 2 and len(llm.calls[0]["images"]) == 1 and llm.calls[1]["images"] == []
    schema = out["form_schema"]
    assert [(w["type"], w["value"], w["filled"]) for w in schema] == [("text", "Ada Lovelace", True), ("checkbox", "True", True)]
    box = schema[0]["bbox"]
    assert box["page"] == 1 and box["top"] >= 116 / 792 and box["top"] + box["height"] <= 160 / 792   # under the label
    with pdfplumber.open(out["document_url"]) as doc:
        page = doc.pages[0]
        words = {w["text"]: w for w in page.extract_words()}
        assert "Ada" in words and 116 <= words["Ada"]["top"] and words["Ada"]["bottom"] <= 162
        assert 60 <= words["Ada"]["x0"] <= 80
        assert len(page.lines) + len(page.curves) >= 2          # the check mark
    with pikepdf.open(out["document_url"]) as pdf:
        streams = b"".join(c.read_bytes() for c in pdf.pages[0].Contents) if isinstance(pdf.pages[0].Contents, pikepdf.Array) \
            else pdf.pages[0].Contents.read_bytes()
        assert b"1.000 0.000 0.000 rg" in streams               # drawn in edit_options.color, red by default
    assert out["usage"]["model_usage"]["models"]["task:edit"]["calls"] == 2
    # the returned schema can be passed back: no detection, no model call, same result
    again = FakeLLM()
    out2 = edit.run(None, {"edit_instructions": "ignored", "form_schema": schema}, llm=again, source_path=flat_pdf,
                    output_path=tmp_path / "out2.pdf")
    assert again.calls == [] and [w["value"] for w in out2["form_schema"]] == ["Ada Lovelace", "True"]


def test_flat_pdf_where_no_field_is_found_is_a_clear_error(flat_pdf, tmp_path):
    _, png = form_image()
    with pytest.raises(TaskError) as e:
        edit.run(None, {"edit_instructions": "Fill the name"}, llm=FakeLLM([{"fields": []}]), source_path=flat_pdf,
                 output_path=tmp_path / "out.pdf", options={"render": lambda path, page: png})
    assert e.value.status_code == 422 and "form_schema" in e.value.detail
    assert e.value.to_response() == {"detail": e.value.detail}


def test_placeholders_are_not_written_and_text_is_fitted_to_its_box(flat_pdf, tmp_path):
    schema = [{"bbox": {"left": 0.25, "top": 0.2, "width": 0.2, "height": 0.02, "page": 1}, "description": "Trading name", "type": "text"},
              {"bbox": {"left": 0.25, "top": 0.3, "width": 0.2, "height": 0.02, "page": 1}, "description": "Address", "type": "text"}]
    llm = FakeLLM({"fields": [{"id": 0, "value": "None"}, {"id": 1, "value": "2100 Harbor Boulevard, Suite 300, Oakland"}]})
    out = edit.run(None, {"edit_instructions": "x", "form_schema": schema, "edit_options": {"flat_fields": False}},
                   llm=llm, source_path=flat_pdf, output_path=tmp_path / "out.pdf")
    assert [(w["value"], w["filled"]) for w in out["form_schema"]] == [(None, False), ("2100 Harbor Boulevard, Suite 300, Oakland", True)]
    with pdfplumber.open(out["document_url"]) as doc:
        chars = [c for c in doc.pages[0].chars if c["text"] == "B"]
        words = doc.pages[0].extract_words()
    assert "None" not in [w["text"] for w in words]
    assert chars and chars[0]["size"] < 12 * 0.65 + 2            # shrunk below the automatic size
    assert max(w["x1"] for w in words if w["top"] > 230) <= (0.25 + 0.2) * 612 + 6   # and it stays inside the box


def test_edit_request_errors(form_pdf, tmp_path):
    cases = [({}, form_pdf), ({"edit_instructions": "x"}, tmp_path / "missing.pdf"),
             ({"edit_instructions": "x", "edit_options": {"color": "red"}}, form_pdf),
             ({"edit_instructions": "x", "form_schema": [{"bbox": {"left": 0}, "description": "a", "type": "text"}]}, form_pdf),
             ({"edit_instructions": "x", "form_schema": [{"bbox": {"left": 0, "top": 0, "width": 1, "height": 1, "page": 4},
                                                          "description": "a", "type": "text"}]}, form_pdf),
             ({"edit_instructions": "x", "form_schema": [{"bbox": {"left": 0, "top": 0, "width": 1, "height": 1, "page": 1},
                                                          "description": "a", "type": "slider"}]}, form_pdf)]
    for config, src in cases:
        with pytest.raises(TaskError) as e:
            edit.run(None, config, llm=FakeLLM(), source_path=src, output_path=tmp_path / "o.pdf")
        assert e.value.status_code == 400
    txt = tmp_path / "notes.txt"
    txt.write_text("hello")
    with pytest.raises(TaskError) as e:
        edit.run(None, {"edit_instructions": "x"}, llm=FakeLLM(), source_path=txt)
    assert e.value.status_code == 415


def test_default_output_path_is_a_new_file(form_pdf):
    out = edit.run(None, {"edit_instructions": "x"}, llm=FakeLLM(mapper()), source_path=form_pdf)
    assert out["document_url"].endswith("form.edited.pdf") and out["document_url"] != str(form_pdf)
    assert field_values(out["document_url"])["dob"][0] == "04/12/1990"


# ---------------------------------------------------------------- DOCX

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W14 = "http://schemas.microsoft.com/office/word/2010/wordml"


def build_docx(path):
    import docx
    from docx.oxml import parse_xml

    d = docx.Document()
    d.add_paragraph("Patient intake form")

    def sdt(alias, inner, extra=""):
        return parse_xml(
            f'<w:p xmlns:w="{W}" xmlns:w14="{W14}"><w:r><w:t xml:space="preserve">{alias}: </w:t></w:r>'
            f'<w:sdt><w:sdtPr><w:alias w:val="{alias}"/><w:tag w:val="{alias.lower().replace(" ", "_")}"/>{extra}'
            f'</w:sdtPr><w:sdtContent>{inner}</w:sdtContent></w:sdt></w:p>')

    placeholder = ('<w:r><w:rPr><w:rStyle w:val="PlaceholderText"/></w:rPr><w:t>Click here</w:t></w:r>'
                   '<w:r><w:t> to enter text.</w:t></w:r>')
    body = d.element.body
    body.insert(len(body) - 1, sdt("Patient name", placeholder, "<w:showingPlcHdr/>"))
    body.insert(len(body) - 1, sdt("Consent", "<w:r><w:t>☐</w:t></w:r>",
                                   '<w14:checkbox><w14:checked w14:val="0"/></w14:checkbox>'))
    body.insert(len(body) - 1, sdt("Blood group", "<w:r><w:t>Choose an item.</w:t></w:r>",
                                   '<w:showingPlcHdr/><w:dropDownList><w:listItem w:displayText="A+" w:value="a_pos"/>'
                                   '<w:listItem w:displayText="O-" w:value="o_neg"/></w:dropDownList>'))
    table = d.add_table(rows=3, cols=3)
    for i, h in enumerate(("Vaccine", "Date given", "Clinic")):
        table.rows[0].cells[i].text = h
    table.rows[1].cells[0].text = "MMR"
    table.rows[2].cells[0].text = "Tetanus"
    table.rows[2].cells[2].text = "City Clinic"
    d.save(str(path))


def docx_state(path):
    import docx
    from docx.oxml.ns import qn
    d = docx.Document(str(path))
    sdts = {}
    for s in d.element.body.iter(qn("w:sdt")):
        alias = s.find(qn("w:sdtPr")).find(qn("w:alias")).get(qn("w:val"))
        sdts[alias] = s
    text = {k: "".join(t.text or "" for t in s.iter(qn("w:t"))) for k, s in sdts.items()}
    return d, sdts, text


def test_docx_content_controls_and_table_cells(tmp_path):
    from docx.oxml.ns import qn
    src = tmp_path / "intake.docx"
    build_docx(src)
    values = {"patient name": "Ravi Kumar", "consent": True, "blood group": "o-",
              "mmr / date given": "2024-05-01", "tetanus / date given": "2023-11-20", "mmr / clinic": "North Clinic"}
    llm = FakeLLM(mapper(values))
    out = edit.run(None, {"edit_instructions": "Fill the intake form.", "edit_options": {"color": "#00FF00"}},
                   llm=llm, source_path=src, output_path=tmp_path / "out.docx", options={"docx_color_mode": "highlight"})
    assert out["form_schema"] is None and out["document_url"].endswith("out.docx")
    shown = [json.loads(line) for line in llm.calls[0]["user"].split("Fields:\n")[1].split("\n\nReturn")[0].splitlines()]
    assert [f["type"] for f in shown[:3]] == ["text", "checkbox", "dropdown"]
    assert shown[2]["options"] == ["A+", "O-"]
    assert [f["description"] for f in shown[3:]] == [
        "table 1, row 2: MMR / Date given", "table 1, row 2: MMR / Clinic", "table 1, row 3: Tetanus / Date given"]

    d, sdts, text = docx_state(out["document_url"])
    assert text == {"Patient name": "Ravi Kumar", "Consent": "☒", "Blood group": "O-"}
    name_pr = sdts["Patient name"].find(qn("w:sdtPr"))
    assert name_pr.find(qn("w:showingPlcHdr")) is None            # no longer a placeholder
    run = next(sdts["Patient name"].iter(qn("w:r")))
    assert run.find(qn("w:rPr")).find(qn("w:rStyle")) is None      # placeholder style removed
    assert run.find(qn("w:rPr")).find(qn("w:shd")).get(qn("w:fill")) == "00FF00"   # highlight colour
    checked = sdts["Consent"].find(qn("w:sdtPr")).find(f"{{{W14}}}checkbox").find(f"{{{W14}}}checked")
    assert checked.get(f"{{{W14}}}val") == "1"
    t = d.tables[0]
    assert [c.text for c in t.rows[1].cells] == ["MMR", "2024-05-01", "North Clinic"]
    assert [c.text for c in t.rows[2].cells] == ["Tetanus", "2023-11-20", "City Clinic"]   # existing text kept
    shd = t.rows[1].cells[1].paragraphs[0].runs[0]._r.find(qn("w:rPr")).find(qn("w:shd"))
    assert shd.get(qn("w:fill")) == "00FF00"
    assert docx_state(src)[2]["Patient name"] == "Click here to enter text."   # source untouched


def test_docx_dropdown_skips_unknown_option_and_text_colour_mode(tmp_path):
    from docx.oxml.ns import qn
    src = tmp_path / "intake.docx"
    build_docx(src)
    out = edit.run(None, {"edit_instructions": "x"}, llm=FakeLLM(mapper({"blood group": "Z+", "patient name": "Mo"})),
                   source_path=src, output_path=tmp_path / "out.docx")  # default: text colour, like Reducto's output
    _, sdts, text = docx_state(out["document_url"])
    assert text["Blood group"] == "Choose an item." and text["Patient name"] == "Mo"
    rpr = next(sdts["Patient name"].iter(qn("w:r"))).find(qn("w:rPr"))
    assert rpr.find(qn("w:color")).get(qn("w:val")) == "FF0000" and rpr.find(qn("w:shd")) is None
    with pytest.raises(TaskError) as e:
        edit.run(None, {"edit_instructions": "x", "form_schema": [{"bbox": {}, "description": "a", "type": "text"}]},
                 llm=FakeLLM(), source_path=src, output_path=tmp_path / "o.docx")
    assert e.value.status_code == 400 and "only works for PDFs" in e.value.detail
