"""Prompts for page and region reads."""

# Same wording as spike/run_claude.py (the prompt behind the 80.4 Haiku baseline).
PAGE_RULES = (
    "Rules: output every piece of body text exactly as written (do not summarize, translate, or fix spelling); "
    "render tables as HTML <table> with rowspan/colspan where needed; write math as LaTeX; "
    "omit running page headers, footers and page numbers; describe figures in one short line. "
    "Output only the Markdown, with no preamble and no code fences."
)
PAGE_PROMPT_INLINE = "Transcribe this document page image to Markdown, in natural reading order. " + PAGE_RULES
PAGE_PROMPT_FILE = ("Read the document page image at {img} and transcribe it to Markdown, "
                    "in natural reading order. " + PAGE_RULES)

# ---- region crops (escalation re-reads) ----
_CROP_COMMON = ("Output every character exactly as printed (do not summarize, translate, or fix spelling). "
                "Output only the result, with no preamble and no code fences.")
TABLE_CROP_PROMPT_FILE = (
    "Read the image at {img}. It is a crop of one table from a document page. Transcribe the table as one HTML "
    "<table>, one <tr> per printed row and one cell per printed cell, with rowspan/colspan where cells are merged "
    "and <th> for header cells. Do not add, drop or reorder rows or columns; leave empty cells empty. "
    "If a caption or note is visible, put it as plain text before or after the table. " + _CROP_COMMON)
TEXT_CROP_PROMPT_FILE = (
    "Read the image at {img}. It is a crop of one region of a document page. Transcribe it to Markdown in natural "
    "reading order; render tables as HTML <table>; write math as LaTeX. " + _CROP_COMMON)

# ---- live profile judged unfiltered (headers/footers kept, checkbox state shown) ----
PAGE_RULES_FULL = (
    "Rules: output every piece of text exactly as printed, including page headers, footers and page numbers "
    "(do not summarize, translate, fix spelling, or reformat numbers and dates); "
    "render tables as HTML <table> with rowspan/colspan where needed, every value in its own row and column; "
    "keep each form label next to its value; show a checkbox as [x] when it is checked and [ ] when it is not; "
    "transcribe handwriting as written and write [illegible] where it cannot be read; never guess or add text "
    "that is not on the page; write math as LaTeX; describe figures in one short line. "
    "Output only the Markdown, with no preamble and no code fences."
)
PAGE_PROMPT_FULL_FILE = ("Read the document page image at {img} and transcribe it to Markdown, "
                         "in natural reading order. " + PAGE_RULES_FULL)

# ---- engine (2026-11-22): the page read labels page furniture itself; mdblocks turns the tags into block types ----
# Generic wording on purpose (no document-specific cases). The text is kept, only typed (docs/decisions.md).
FURNITURE_TAG_RULE = (
    "Running page headers, running footers, page numbers and line numbers printed in a page margin are page "
    "furniture, not body text: write each one on its own line, separated from other text by blank lines, wrapped "
    "as <page-header>...</page-header>, <page-footer>...</page-footer>, <page-number>...</page-number> or "
    "<line-number>...</line-number>, with its text exactly as printed. Everything else on the page, including "
    "titles, headings, footnotes and captions, is body text. ")
PAGE_PROMPT_LABELLED_FILE = PAGE_PROMPT_FULL_FILE.replace("Rules: ", "Rules: " + FURNITURE_TAG_RULE, 1)

TABLE_CROP_STRICT_FILE = (
    "Read the image at {img}. It is an enlarged crop of one table from a document page. Transcribe the table as "
    "one HTML <table>: one <tr> per printed row, one cell per printed cell, rowspan/colspan where cells are merged, "
    "<th> for header cells, empty cells left empty. Copy every digit, sign, comma and decimal point exactly as "
    "printed; read each number character by character and keep it in the row and column where it is printed. "
    "Do not add, drop, merge or reorder rows or columns, and do not compute or correct anything. "
    "Where a cell cannot be read, write [illegible] in that cell instead of guessing. "
    "Text that is visible above or below the table but is not part of it must be left out. "
    "Output only the HTML table, with no preamble and no code fences."
)

# ---- engine v2 (2026-11-12): one page read, form-aware, plus zoomed strip reads on scans ----
FORM_RULE = (
    "Use an HTML <table> only for a true data table: a regular grid of rows and columns under column headers. "
    "A form made of labelled boxes is not a table and must not be forced into one. For a form, write each field on "
    "its own line as `label: value`, keeping the field's printed number or letter and the label exactly as printed "
    "and leaving the value empty when the field is blank; go through the fields in reading order, left to right and "
    "top to bottom, finishing one box before starting the next; keep each group of checkboxes on one line with every "
    "option followed by its own [x] or [ ]. A grid section inside a form (for example numbered service lines with "
    "columns) is still written as an HTML table. ")
PAGE_PROMPT_V2_FILE = ("Read the document page image at {img} and transcribe it to Markdown, in natural reading "
                       "order. " + FORM_RULE + PAGE_RULES_FULL)
STRIP_PROMPT_FILE = (
    "Read the image at {img}. It is an enlarged horizontal strip of a scanned document page (the strip may cut "
    "through a table). Transcribe everything visible to Markdown in natural reading order. Render tables as HTML "
    "<table>, one <tr> per printed row, every value in its own cell. Copy every digit, sign, comma and decimal point "
    "exactly as printed, reading each number character by character. Transcribe handwriting as written; write "
    "[illegible] only where a character truly cannot be read. Never guess or add text that is not visible. "
    "Output only the Markdown, with no preamble and no code fences.")


def inline(prompt: str) -> str:
    """The same prompt for readers that attach the image to the message instead of naming a file."""
    return (prompt.replace("Read the document page image at {img} and transcribe it", "Transcribe this document page image")
            .replace("Read the image at {img}. It is", "The image is"))


# ---- table-only reads of a table region ----
# TABLE_ONLY_FILE is the wording of harness/run_model.py (91.4 on the 100 RD-TableBench tables through the CLI).
_TABLE_ONLY_CORE = (
    "Specify rowspan and colspan attributes when they are greater than 1. Do not specify any other attributes. "
    "Only use table related HTML tags, no additional formatting is required. ")
TABLE_ONLY_FILE = (
    "Read the image at {img}. It is a table from a document page. Convert the table in the image to an HTML "
    "table. The output should begin with <table> and end with </table>. " + _TABLE_ONLY_CORE +
    "Output only the HTML, nothing else.")
# The same with the cell rules of the page prompt (exact text, checkbox marks, unreadable cells) and cell hygiene.
TABLE_ONLY_CLEAN_FILE = (
    "Read the image at {img}. It is a table from a document page. Convert the table in the image to an HTML "
    "table. The output should begin with <table> and end with </table>. " + _TABLE_ONLY_CORE +
    "Copy the text of every cell exactly as printed (do not summarize, translate, correct or compute anything), "
    "one <tr> per printed row and one cell per printed cell; leave empty cells empty. A cell that holds only a "
    "picture, icon or logo stays empty: do not describe it. Write a list inside a cell as plain lines with the "
    "printed bullet, separated by <br>. Show a checkbox as [x] when it is checked and [ ] when it is not. Where a "
    "cell cannot be read, write [illegible]. Text above or below the table that is not part of it must be left "
    "out. Output only the HTML, nothing else.")
# For a region the layout model calls a table while the page read wrote it as running text: the model may say no.
TABLE_MAYBE_FILE = (
    "Read the image at {img}. It is a region of a document page that may hold a table (rows and columns, with or "
    "without ruling lines). If it is a table, convert it to an HTML table: the output should begin with <table> "
    "and end with </table>. " + _TABLE_ONLY_CORE +
    "Copy the text of every cell exactly as printed; leave empty cells empty; a cell that holds only a picture or "
    "icon stays empty. If the region is not a table (running text, a list of paragraphs, a figure, a form of "
    "labelled boxes), output exactly NOT_A_TABLE. Output only the HTML or NOT_A_TABLE, nothing else.")

# ---- one displayed formula read from its crop ----
FORMULA_CROP_FILE = (
    "Read the image at {img}. It is a crop of one displayed formula from a document page (it may be a chain of "
    "matrices, a system of equations or an equation printed over several lines). Transcribe it as LaTeX inside one "
    "$$ ... $$ block, exactly as printed: write a matrix as \\begin{bmatrix} ... \\end{bmatrix} (or pmatrix / vmatrix "
    "for round brackets / bars), never as a table; when the formula is printed over several lines, keep one line "
    "per printed line inside \\begin{aligned} ... \\end{aligned}, ending each line with \\\\; write an equation "
    "number printed at the margin as \\tag{n}. Text that is not part of the formula must be left out. If the crop "
    "shows no formula, output exactly NO_FORMULA. Output only the LaTeX, with no preamble and no code fences.")
