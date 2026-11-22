"""Pipeline configurations compared during development (see research/README.md).

Importing this module registers every configuration with ``readanydoc.profiles``. A server picks them up with
READANYDOC_PROFILE_MODULES=readanydoc_research.profiles. ``engine-final-4`` is the configuration shipped as the
``engine`` profile, and ``vanilla-api`` the one shipped as ``model-only``. Profiles named ``cached-*`` replay saved
model outputs from the spike/ folder of the development workspace instead of calling a model.
"""

from __future__ import annotations

from readanydoc import profiles as _registry
from readanydoc.pipeline import PipelineConfig, StageConfig

PROFILES: dict[str, PipelineConfig] = {
    # Haiku page transcriptions (80.4 baseline) on PP-DocLayout regions and PP-OCR/PDF word boxes.
    "cached-haiku": PipelineConfig(profile="cached-haiku", tier="strong", reader="cached:haiku",
                                   layout="cached:pvl", words="cached:ppocr"),
    # GLM-OCR page transcriptions from PDF input (71.3 baseline); same layout and word sources.
    "cached-glm": PipelineConfig(profile="cached-glm", tier="fast", reader="cached:glm_pdf",
                                 layout="cached:pvl", words="cached:ppocr"),
    # ---- workstream A ----
    # Quality: two Haiku page reads (native-size image first, 150-dpi second), one chosen per page
    # by agreement with PDF/OCR words; furniture by consensus; tables the
    # two reads dispute are re-read as crops and the best-supported candidate is kept.
    "quality": PipelineConfig(profile="quality", tier="strong", reader="cached:haiku_hires",
                              layout="cached:pvl", words="cached:ppocr",
                              stages={"read": StageConfig(options={"candidates": ["cached:haiku"],
                                                                  "table_reader": "crop_cache:haiku"})}),
    # Cheap: GLM-OCR page read; pages whose fast reads dispute many words go to Haiku (native size),
    # tables that fail verification are re-read by Haiku as crops (cached under spike/out/haiku_crops).
    "cheap": PipelineConfig(profile="cheap", tier="fast", reader="cached:glm_pdf",
                            layout="cached:pvl", words="cached:ppocr",
                            stages={"read": StageConfig(options={"escalate": {
                                "reader": "cached:haiku_hires", "table_reader": "crop_cache:haiku"}}),
                                    "verify": StageConfig(options={"fusion": True})}),
    # Live quality on any document: two independent native-size Haiku reads per page (disk-cached),
    # evidence = trusted PDF text layer, else local Tesseract words; no layout detector needed.
    "live-quality": PipelineConfig(profile="live-quality", tier="strong", reader="live_cache:haiku#1",
                                   layout="cached:pvl", words="tesseract",
                                   stages={"layout": StageConfig(enabled=False),
                                           # judged on 147 business pages (Parse Judge, 2026-11-11): the
                                           # layout-free table swap lost 19 pages and won 4 -> off; turning
                                           # the reader's HTML tables into markdown lost 12 and won 0 -> off
                                           "read": StageConfig(options={"candidates": ["live_cache:haiku#2"],
                                                                        "table_select_no_layout": False}),
                                           "shape": StageConfig(options={"dynamic_tables": False})}),
    # live-quality plus GPU-free table repair: Haiku finds the tables (pixel boxes), each one is re-read
    # from a high-resolution crop, and the crop replaces the page-read table only when digits or column
    # totals give it a clear margin; otherwise the table is marked low confidence.
    "live-quality-tables": PipelineConfig(
        profile="live-quality-tables", tier="strong", reader="live_cache:haiku#1", layout="claude:haiku",
        words="tesseract",
        stages={"read": StageConfig(options={"candidates": ["live_cache:haiku#2"], "table_select_no_layout": False,
                                             "table_mode": "crop_numeric", "table_reader": "live_cache:haiku#crop",
                                             "crop_zoom": 0, "crop_max_dpi": 300, "max_table_crops": 3}),
                "shape": StageConfig(options={"dynamic_tables": False})}),
    # Opt-in: expert tier (Sonnet) as arbiter only. Disputed tables get one expert crop read and a
    # cell-level majority; handwriting-likely pages get one expert page read and a 2-of-3 token majority.
    # The expert read is never used alone.
    "live-quality-expert": PipelineConfig(
        profile="live-quality-expert", tier="strong", reader="live_cache:haiku#1", layout="claude:haiku",
        words="tesseract",
        stages={"read": StageConfig(options={"candidates": ["live_cache:haiku#2"], "table_select_no_layout": False,
                                             "table_mode": "crop_numeric", "table_reader": "live_cache:haiku#crop",
                                             "crop_zoom": 0, "crop_max_dpi": 300, "max_table_crops": 3,
                                             "expert_table_reader": "live_cache:sonnet#crop",
                                             "expert_page_reader": "live_cache:sonnet#1"}),
                "verify": StageConfig(options={"majority": True}),
                "shape": StageConfig(options={"dynamic_tables": False})}),
    # Cost-reduced: one Haiku read; table detection, crops and the expert arbiter only on pages without
    # an exact text layer. With two crop reads as the only voters, a cell changes only when the strong
    # crop and the expert crop agree against the page read.
    "live-lean": PipelineConfig(
        profile="live-lean", tier="strong", reader="live_cache:haiku#1", layout="claude:haiku", words="tesseract",
        stages={"layout": StageConfig(options={"only_untrusted": True}),
                "read": StageConfig(options={"table_mode": "crop_numeric", "table_reader": "live_cache:haiku#crop",
                                             "crop_zoom": 0, "crop_max_dpi": 300, "max_table_crops": 3,
                                             "expert_table_reader": "live_cache:sonnet#crop"}),
                "shape": StageConfig(options={"dynamic_tables": False})}),
    "live-lean-no-expert": PipelineConfig(
        profile="live-lean-no-expert", tier="strong", reader="live_cache:haiku#1", layout="claude:haiku",
        words="tesseract",
        stages={"layout": StageConfig(options={"only_untrusted": True}),
                "read": StageConfig(options={"table_mode": "crop_numeric", "table_reader": "live_cache:haiku#crop",
                                             "crop_zoom": 0, "crop_max_dpi": 300, "max_table_crops": 3}),
                "shape": StageConfig(options={"dynamic_tables": False})}),
    # Engine v2 (2026-11-12). One Haiku page read at native size. A read that shows checkboxes inside
    # tables is a fill-in form forced into a grid: it is read again with the form prompt. Pages without
    # an exact text layer whose scan has more pixels than a page read keeps are read again as enlarged
    # strips, and a character changes only when two strip reads agree against the page read.
    # No layout model, no GPU, no second model.
    "engine2": PipelineConfig(
        profile="engine2", tier="strong", reader="live_cache:haiku#1", layout="cached:pvl", words="tesseract",
        stages={"layout": StageConfig(enabled=False),
                "read": StageConfig(options={"page_prompt": "PAGE_PROMPT_FULL_FILE", "form_reread": True,
                                             "form_reader": "live_cache:haiku#v5form", "zoom_vote": True,
                                             "zoom_reader": "live_cache:haiku#v5band", "page_workers": 4,
                                             "table_select_no_layout": False}),
                "shape": StageConfig(options={"dynamic_tables": False})}),
    # The same engine for deployment: every read goes through the configured reader
    # (READANYDOC_READER=anthropic_api:haiku with your own key, or the default Claude Code CLI).
    "engine-v2": PipelineConfig(
        profile="engine-v2", tier="strong", reader="claude_cli:haiku", layout="cached:pvl", words="tesseract",
        stages={"layout": StageConfig(enabled=False),
                "read": StageConfig(options={"page_prompt": "PAGE_PROMPT_FULL_FILE", "form_reread": True,
                                             "zoom_vote": True, "table_select_no_layout": False,
                                             "page_workers": 4}),
                "shape": StageConfig(options={"dynamic_tables": False})}),
    # Through the Anthropic API with your own key, at high effort (measured 2026-11-12: default effort
    # scores lower, low effort much lower). No form re-read: at high effort a single read already handles
    # forms and the re-read scored slightly worse. Zoom vote on scans only.
    # Handwriting route (option ``handwriting_route``): a scan whose Haiku read has an [illegible] mark, or
    # that local OCR cannot read at all, is read once by Sonnet 5.5 at default effort and that read is the
    # page (18 handwriting pages, judge Opus: Sonnet 8.06, Haiku 5.61, Reducto r-1 5.67). No zoom vote there.
    "engine-api": PipelineConfig(
        profile="engine-api", tier="strong", reader="anthropic_api:haiku#high", layout="cached:pvl", words="tesseract",
        stages={"layout": StageConfig(enabled=False),
                "read": StageConfig(options={"page_prompt": "PAGE_PROMPT_FULL_FILE", "zoom_vote": True,
                                             "handwriting_route": True, "handwriting_reader": "anthropic_api:sonnet",
                                             "table_select_no_layout": False, "page_workers": 6}),
                "ground": StageConfig(options={"page_numbers": False}),
                "shape": StageConfig(options={"dynamic_tables": False})}),
    # Baseline for every comparison: one Haiku page read through the API at high effort, nothing else.
    "vanilla-api": PipelineConfig(
        profile="vanilla-api", tier="strong", reader="anthropic_api:haiku#high", layout="cached:pvl", words="tesseract",
        stages={"layout": StageConfig(enabled=False),
                "read": StageConfig(options={"page_prompt": "PAGE_PROMPT_FULL_FILE", "table_select_no_layout": False,
                                             "page_workers": 6}),
                "ground": StageConfig(options={"page_numbers": False}),
                "shape": StageConfig(options={"dynamic_tables": False})}),
    # Final round, workstream 1 (LAYOUT): vanilla-api plus an open layout model on CPU (Docling heron through
    # ONNX Runtime; READANYDOC_LAYOUT=onnx:ppdoclayout_plus for the other one). Every use of the regions is a
    # switch that is off: with no switch (ground option ``regions`` / env READANYDOC_REGIONS, see
    # pipeline/regions/__init__.py) the output is vanilla-api's and the model is not loaded.
    "layout-api": PipelineConfig(
        profile="layout-api", tier="strong", reader="anthropic_api:haiku#high", layout="onnx:heron", words="tesseract",
        stages={"layout": StageConfig(options={"detect_only": True}),
                "read": StageConfig(options={"page_prompt": "PAGE_PROMPT_FULL_FILE", "table_select_no_layout": False,
                                             "page_workers": 6}),
                "ground": StageConfig(impl="ground_regions", options={"page_numbers": False}),
                "shape": StageConfig(options={"dynamic_tables": False})}),
    # layout-api with the switches that were never worse than vanilla-api on any devset measure, dev and held
    # (e2e/devset/ws1_report.md): guarded types, layout boxes, crop repair of tables with a broken grid, guarded
    # form crops. Not a default anywhere: selected only by name (READANYDOC_PROFILE=layout-best-api).
    "layout-best-api": PipelineConfig(
        profile="layout-best-api", tier="strong", reader="anthropic_api:haiku#high", layout="onnx:heron", words="tesseract",
        stages={"layout": StageConfig(options={"detect_only": True}),
                "read": StageConfig(options={"page_prompt": "PAGE_PROMPT_FULL_FILE", "table_select_no_layout": False,
                                             "page_workers": 6}),
                "ground": StageConfig(impl="ground_regions",
                                      options={"page_numbers": False,
                                               "regions": "types=guarded,boxes,tables=broken,forms=notables"}),
                "shape": StageConfig(options={"dynamic_tables": False})}),
    # Final round, workstream 4 (OCR, page preparation, speed, cost): vanilla-api plus the switches that were kept
    # (e2e/devset/ws4_report.md). Needs the optional extra ``ocr`` (pip install 'readanydoc[ocr]').
    #   words tesseract>rapidocr   PP-OCR places the blocks Tesseract cannot (boxes only; scans and images)
    #   triage.prepare rotate      turned pages are put upright before anything reads them
    #   zoom vote with gates       strips only where local OCR sees 20+ numbers, started with the page read; the
    #                              second set and the vote only for a legible numeric table
    #   page_workers 16            pages of a document read together (READANYDOC_PAGE_WORKERS overrides)
    "vanilla-api-ws4": PipelineConfig(
        profile="vanilla-api-ws4", tier="strong", reader="anthropic_api:haiku#high", layout="cached:pvl",
        words="tesseract>rapidocr",
        stages={"layout": StageConfig(enabled=False),
                "triage": StageConfig(options={"prepare": "rotate"}),
                "read": StageConfig(options={"page_prompt": "PAGE_PROMPT_FULL_FILE", "table_select_no_layout": False,
                                             "page_workers": 16, "zoom_vote": True, "zoom_pre_gate": 20,
                                             "zoom_prefetch": 1, "zoom_gate": "table_legible"}),
                "ground": StageConfig(options={"page_numbers": False}),
                "shape": StageConfig(options={"dynamic_tables": False})}),
    # Final engine (combine phase of the final round, e2e/devset/final_report.md): the pieces of the four workstreams
    # that held up when added one at a time on the same cached page reads, plus the handwriting route.
    # Every piece is one option and can be switched off on its own (profile option, or for a server
    # READANYDOC_STAGE_OPTIONS='{"<stage>": {"<option>": <value>}}' / READANYDOC_WORDS):
    #   words tesseract>rapidocr            ws4  PP-OCR places what Tesseract cannot       off: READANYDOC_WORDS=tesseract
    #   triage.prepare rotate               ws4  turned pages are put upright              off: {"triage": {"prepare": ""}}
    #     prepare_method safe                    guarded: "auto" turned 5 upright handwritten devset pages
    #   read.zoom_vote + gates + prefetch   ws4  strips only for legible numeric scans     off: {"read": {"zoom_vote": false}}
    #   read.handwriting_route              D    handwritten scans are read by Sonnet 5.5  off: {"read": {"handwriting_route": false}}
    #     handwriting_illegible_max_agreement    guarded: an [illegible] mark on a page that local OCR confirms
    #                                            (mostly print) no longer sends the page to Sonnet
    #   read.forms                          ws2  fillable-PDF field values, label joins,   off: {"read": {"forms": ""}}
    #                                            form grids as Key Value blocks, one per row (kv_rules:rows)
    #   verify.checks                       ws3  grid repair, totals re-read, flags        off: {"verify": {"checks": ""}}
    #   ground.regions                      ws1  guarded types and boxes from the layout   off: {"ground": {"regions": ""}}
    #                                            model (heron, CPU), text blocks split at region borders,
    #                                            guarded form crops
    # Table repair is workstream 3's (verify.checks grid=norm+draft); workstream 1's ``tables=broken`` is left out:
    # on the same reads it repaired far fewer tables, and after workstream 3's repair it changes nothing.
    # Needs the extras ``ocr`` and ``layout``.
    "engine-final": PipelineConfig(
        profile="engine-final", tier="strong", reader="anthropic_api:haiku#high", layout="onnx:heron",
        words="tesseract>rapidocr",
        stages={"triage": StageConfig(options={"prepare": "rotate", "prepare_method": "safe"}),
                "layout": StageConfig(options={"detect_only": True}),
                "read": StageConfig(options={"page_prompt": "PAGE_PROMPT_FULL_FILE", "table_select_no_layout": False,
                                             "page_workers": 16, "zoom_vote": True, "zoom_pre_gate": 20,
                                             "zoom_prefetch": 1, "zoom_gate": "table_legible",
                                             "handwriting_route": True, "handwriting_reader": "anthropic_api:sonnet",
                                             "handwriting_illegible_max_agreement": 0.5,
                                             "forms": "fillable,kv_lines:join,kv_rules:rows"}),
                "verify": StageConfig(options={"checks": "grid=norm+draft;numbers=1;flags=1"}),
                "ground": StageConfig(impl="ground_regions",
                                      options={"page_numbers": False,
                                               "regions": "types=guarded,boxes,split,forms=notables"}),
                "shape": StageConfig(options={"dynamic_tables": False})}),
    # Round 2, workstream A (e2e/devset/r2a_report.md): engine-final plus the table, formula and zoom switches that
    # were never worse than engine-final beyond noise on any devset measure, dev and held, on the same page reads.
    # Not a default: selected only by name (READANYDOC_PROFILE=engine-final-r2a). Each piece can be switched off:
    #   ground.tables   reread=missed   layout shows a table where the read wrote running text: table-only re-read,
    #                                   accepted as a real grid with the same words whose cells stand in columns
    #                   reread=broken   tables still broken after verify's grid repair
    #                   reread=all      every table with a layout region: table-only re-read, accepted only with a
    #                                   consistent grid, exactly the same numbers and 95% of the same words
    #                   clean=1         no picture descriptions and no list markup inside table cells
    #                   formulas=matrix a matrix the read wrote as a table is read again as a formula
    #                   prefetch=1      the table reads start together with the page read (same output)
    #                                   off: READANYDOC_TABLES="" (or leave single keys out)
    #   read.zoom_regions gate          no zoom strips on pages without a table or form region of the layout
    #                                   off: {"read": {"zoom_regions": ""}}
    "engine-final-r2a": PipelineConfig(
        profile="engine-final-r2a", tier="strong", reader="anthropic_api:haiku#high", layout="onnx:heron",
        words="tesseract>rapidocr",
        stages={"triage": StageConfig(options={"prepare": "rotate", "prepare_method": "safe"}),
                "layout": StageConfig(options={"detect_only": True}),
                "read": StageConfig(options={"page_prompt": "PAGE_PROMPT_FULL_FILE", "table_select_no_layout": False,
                                             "page_workers": 16, "zoom_vote": True, "zoom_pre_gate": 20,
                                             "zoom_prefetch": 1, "zoom_gate": "table_legible", "zoom_regions": "gate",
                                             "handwriting_route": True, "handwriting_reader": "anthropic_api:sonnet",
                                             "handwriting_illegible_max_agreement": 0.5,
                                             "forms": "fillable,kv_lines:join,kv_rules:rows"}),
                "verify": StageConfig(options={"checks": "grid=norm+draft;numbers=1;flags=1"}),
                "ground": StageConfig(impl="ground_regions",
                                      options={"page_numbers": False,
                                               "regions": "types=guarded,boxes,split,forms=notables",
                                               "tables": "reread=missed+broken+all;accept=content;missed_prompt=force;"
                                                         "clean=1;formulas=matrix;prefetch=1"}),
                "shape": StageConfig(options={"dynamic_tables": False})}),
}


# Round 2, workstream B (e2e/devset/r2b_report.md): engine-final plus the form and block-structure switches that held
# on three independent sets of page reads. Not a default: selected only by name (READANYDOC_PROFILE=engine-final-r2b).
#   ground.regions  hfsplit        a running head or foot printed on one line becomes one block per word group
#                   formread       each form region is read once from its crop with the form prompt, as evidence only:
#                                  the page read's blocks that are its fields are typed Key Value, a label block and
#                                  its value block are joined; no character of the page read changes
#                   kv=page        on a page without any form region the reader's "Label: text" lines are Text
#                   boxfill        a block without a located box takes the free layout region with its words
#                                  off: leave the name out of the list
#   read.handwriting_illegible_sure_agreement / handwriting_max_ocr_agreement = 0.33
#                                  a printed page that local OCR half-confirms is not sent to the handwriting reader
#                                  for one or two [illegible] marks      off: set both back to None / 0.5
# Options that were measured and left out: kv=form (field-like text in a form region), kvsplit, figures.
PROFILES["engine-final-r2b"] = (
    PROFILES["engine-final"]
    .with_stage("read", handwriting_illegible_sure_agreement=0.33, handwriting_max_ocr_agreement=0.33)
    .with_stage("ground", regions="types=guarded,boxes,split,forms=notables,hfsplit,kv=page,formread,boxfill")
    .model_copy(update={"profile": "engine-final-r2b"}))

# Round 2, combine step (e2e/devset/final_report_r2.md): engine-final plus workstream A's table / formula / zoom set
# and workstream B's form and block-structure set. Each piece keeps its own switch (see the two profiles above).
# Decided in the combine step on three sets of page reads:
#   kvsplit         ON   a Key Value block inside a form region is cut between layout regions where the next line
#                        starts a field. Both business answer keys (100 pages): precision +0.7 to +2.0 dev,
#                        +0.5 to +1.0 held; recall +1.4 to +2.0 dev, 0 to +0.3 held. Price, inside noise: strict
#                        DocLayNet type precision up to -0.7 (Key Value has no label there, so a block that was
#                        counted wrong once is counted wrong twice).   off: leave the name out of ground.regions
#   figures         OFF  shape option layout_figures (workstream C's; B's region switch ``figures`` is superseded):
#                        OmniDocBench layout type precision -0.5 / -1.3 with the running-head rule, held beyond noise.
#                        on: READANYDOC_STAGE_OPTIONS='{"shape": {"layout_figures": true}}'
# Not the server default (the API tests rely on the default profile): select with READANYDOC_PROFILE=engine-final-2.
PROFILES["engine-final-2"] = (
    PROFILES["engine-final-r2a"]
    .with_stage("read", handwriting_illegible_sure_agreement=0.33, handwriting_max_ocr_agreement=0.33)
    .with_stage("ground", regions="types=guarded,boxes,split,forms=notables,hfsplit,kvsplit,kv=page,formread,boxfill")
    .model_copy(update={"profile": "engine-final-2"}))

# Round 3, workstream B (cost, wasted work, time; e2e/devset/r3b_report.md). ``engine-final-2`` is untouched.
# "Cheapest": ONE model call per page (the Haiku page read) and every piece of engine-final-2 that makes no model
# call: layout types and boxes, running heads, Key Value rules, fillable-PDF values, grid normalisation, cell
# hygiene, flags. Off: zoomed strips, the handwriting reader, verify re-reads, table-only re-reads, form reads,
# formula crops. Select with READANYDOC_PROFILE=engine-cheapest.
PROFILES["engine-cheapest"] = (
    PROFILES["engine-final-2"]
    .with_stage("read", zoom_vote=False, zoom_prefetch=0, handwriting_route=False)
    .with_stage("verify", checks="grid=norm;flags=1")
    .with_stage("ground", regions="types=guarded,boxes,split,hfsplit,kvsplit,kv=page,boxfill", tables="clean=1")
    .model_copy(update={"profile": "engine-cheapest"}))


# Round 3 (branch round3-cheap-fixes; e2e/devset/r3a_report.md): engine-final-2 plus the free structure rules of
# workstream A (regions/furniture.py). No model call is added and no character of a page read changes.
#   ground.regions  hfdet      a running head / foot over two or more page-header / page-footer layout detections
#                              standing side by side is cut into one block per detection
#                   pnsplit    a page number at the start or end of a running head / foot becomes its own block when
#                              the page words show it standing clear of the rest
#                   hftable    a table of at most four short cells that the layout calls page furniture: one block per cell
#                   prose      a one-column table of running text where the layout sees no table: text blocks again
#                   lab=paren  a bracketed unit line that the position rule typed Header is Text again
#                              off: leave the name out of ground.regions
# Measured and left out (options): lab=fig, lab=cap, lab=list, lab=long.
# Workstream B's cost / time switches of round 3 are not part of this definition (see its report).
PROFILES["engine-final-3"] = (
    PROFILES["engine-final-2"]
    .with_stage("ground", regions="types=guarded,boxes,split,forms=notables,hfsplit,kvsplit,kv=page,formread,boxfill,"
                                  "hfdet,pnsplit,hftable,prose,lab=paren")
    .model_copy(update={"profile": "engine-final-3"}))

# Round 3, workstream B: engine-final-2 with the cost / time switches that left every response it was measured on
# unchanged (e2e/devset/r3b_report.md). The lead adds workstream A's structure rules on top of this one.
#   read.zoom_second disputed   the second zoomed pass reads only the strips that overlap a first-pass strip with a
#                               disputed token (same vote; fewer strip calls)      off: {"read": {"zoom_second": ""}}
#   ground.tables prefetch_score=0.6;prefetch_area=0.02   table reads start with the page read only for regions
#                               that are nearly always re-read; weak or small regions are read when wanted
#   read.form_prefetch          the form region reads start with the page read instead of after grounding (same
#                               crop, prompt and reader; a form page waits less)   off: {"read": {"form_prefetch": false}}
# Measured and left off (options): read.zoom_effort (strips at low effort lost a verified digit), tables all_if
# (cheaper re-read trigger, loses part of the RD-TableBench gain), verify grid mode ``pre``, a spending cap
# (read.page_cap_usd / settings.page_cap_usd), profile engine-cheapest.
PROFILES["engine-final-3-cost"] = (
    PROFILES["engine-final-2"]
    .with_stage("read", zoom_second="disputed", form_prefetch=True)
    .with_stage("ground", tables=PROFILES["engine-final-2"].stage("ground").options["tables"]
                + ";prefetch_score=0.6;prefetch_area=0.02")
    .model_copy(update={"profile": "engine-final-3-cost"}))


# Round 3, combined by the lead: engine-final-3-cost (same blocks as engine-final-2, less waiting) plus the three
# structure rules that rest on properties of documents and fire often enough to measure: hfdet (running heads cut at
# furniture detections), pnsplit (page number split off a header/footer line), prose (a one-column table of running
# text is text). Left off as options, on purpose: hftable (fired on 3 pages of 639) and lab=paren (inside noise;
# it only undoes one of our own position rules). See e2e/devset/r3a_report.md and r3b_report.md.
PROFILES["engine-final-3-full"] = (
    PROFILES["engine-final-3-cost"]
    .with_stage("ground", regions="types=guarded,boxes,split,forms=notables,hfsplit,kvsplit,kv=page,formread,boxfill,"
                                  "hfdet,pnsplit,prose")
    .model_copy(update={"profile": "engine-final-3-full"}))

# Round 4 (branch round3-cheap-fixes; e2e/rdtb_full/r4/report.md): engine-final-3-full with the two fixes found by the
# full RD-TableBench run (1,000 tables). Nothing else differs. No model call is added; the handwriting reader is
# called on fewer pages.
#   read.forms kv_guard           the form-grid rule (kv_rules) leaves a table alone where the layout model detects a
#                                 Table region with a good score around the table's words: a real data table stays a
#                                 table (5 of 1,000 benchmark tables were turned into Key Value blocks)
#                                 off: {"read": {"forms": "fillable,kv_lines:join,kv_rules:rows"}}
#   read.handwriting_print_ocr    a page goes to the handwriting reader only when the second local OCR (PP-OCR) cannot
#                                 read it as print either (57 of 1,000 printed benchmark tables went to Sonnet)
#                                 off: {"read": {"handwriting_print_ocr": false}}
# The crash fix of round 4 (readanydoc/cvsafe.py, readanydoc/localmodels.py) is in the code for every profile.
PROFILES["engine-final-4"] = (
    PROFILES["engine-final-3-full"]
    .with_stage("read", forms="fillable,kv_lines:join,kv_rules:rows,kv_guard", handwriting_print_ocr=True)
    .model_copy(update={"profile": "engine-final-4"}))

# 2026-11-22: the page read labels page furniture itself (prompts.FURNITURE_TAG_RULE, generic wording); the
# tagged lines become Header / Footer / Page Number blocks in mdblocks. Same one page read, no extra calls.
PROFILES["engine-final-5"] = (
    PROFILES["engine-final-4"].with_stage("read", page_prompt="PAGE_PROMPT_LABELLED_FILE")
    .model_copy(update={"profile": "engine-final-5"}))


# the cheaper sibling of each engine configuration (served for settings.model = r-1-mini / legacy)
CHEAPER = {name: "vanilla-api" for name in ("engine-final", "engine-api", "layout-api", "layout-best-api", "engine-final-2",
                                           "engine-final-r2a", "engine-final-r2b", "engine-final-3-cost",
                                           "engine-cheapest")}

for _name, _config in PROFILES.items():
    _registry.register(_name, _config, CHEAPER.get(_name))
