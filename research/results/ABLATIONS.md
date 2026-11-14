# Ablations: stage on/off -> 5-category olmOCR-bench score

Generated 2026-11-11 by `uv run python -m readanydoc_research bench --ablations --full`. Scope: all 140 pages.
Score = mean of the 5 category pass rates (official olmOCR-bench tests). Delta is against the full pipeline of the same profile.
Bench output = serializer chunk content with `filter_blocks=[Header, Footer, Page Number]`.

## cached-haiku (reader `cached:haiku`, layout `cached:pvl`, words `cached:ppocr`)

| config | score | delta | headers | tiny | multicol | scans | tables |
|---|---|---|---|---|---|---|---|
| raw model output | 80.4 | +0.0 | 91.7 | 83.9 | 88.8 | 45.1 | 92.7 |
| full pipeline | 80.4 | 0.0 | 91.7 | 83.9 | 88.8 | 45.1 | 92.7 |
| - triage off | 80.4 | +0.0 | 91.7 | 83.9 | 88.8 | 45.1 | 92.7 |
| - layout off | 80.4 | +0.0 | 91.7 | 83.9 | 88.8 | 45.1 | 92.7 |
| - read off | 21.3 | -59.2 | 97.2 | 0.0 | 0.0 | 9.2 | 0.0 |
| - verify off | 80.4 | +0.0 | 91.7 | 83.9 | 88.8 | 45.1 | 92.7 |
| - ground off | 80.4 | +0.0 | 91.7 | 83.9 | 88.8 | 45.1 | 92.7 |
| - assemble off | 80.4 | +0.0 | 91.7 | 83.9 | 88.8 | 45.1 | 92.7 |
| - shape off | 67.8 | -12.6 | 30.6 | 83.9 | 88.8 | 43.0 | 92.7 |
| + read.layout_furniture on | 80.1 | -0.3 | 93.1 | 83.9 | 87.9 | 43.0 | 92.7 |
| + read.layout_furniture on, edge band 0.12 | 80.6 | +0.1 | 91.7 | 83.9 | 88.8 | 45.8 | 92.7 |
| - read.segment off (page-level) | 80.4 | +0.0 | 91.7 | 83.9 | 88.8 | 45.1 | 92.7 |
| + verify.fusion on | 80.4 | +0.0 | 91.7 | 83.9 | 88.8 | 45.1 | 92.7 |
| - read.robust off (no retry/trim) | 80.4 | +0.0 | 91.7 | 83.9 | 88.8 | 45.1 | 92.7 |

Side metrics of the full pipeline (not part of the score):

- pages: 140
- errors: 0
- citation_coverage_digital: 92.7
- n_digital: 77
- citation_coverage_scanned: 79.4
- n_scanned: 63
- pages_with_text_layer: 94
- pages_text_layer_usable: 56
- regions: 2248
- regions_with_box_pct: 92.2
- regions_low_confidence_pct: 20.3

## cached-glm (reader `cached:glm_pdf`, layout `cached:pvl`, words `cached:ppocr`)

| config | score | delta | headers | tiny | multicol | scans | tables |
|---|---|---|---|---|---|---|---|
| raw model output | 71.3 | +0.0 | 95.8 | 75.9 | 80.4 | 34.5 | 69.9 |
| full pipeline | 71.3 | 0.0 | 95.8 | 75.9 | 80.4 | 34.5 | 69.9 |
| - triage off | 71.3 | +0.0 | 95.8 | 75.9 | 80.4 | 34.5 | 69.9 |
| - layout off | 71.3 | +0.0 | 95.8 | 75.9 | 80.4 | 34.5 | 69.9 |
| - read off | 21.3 | -50.0 | 97.2 | 0.0 | 0.0 | 9.2 | 0.0 |
| - verify off | 71.3 | +0.0 | 95.8 | 75.9 | 80.4 | 34.5 | 69.9 |
| - ground off | 71.3 | +0.0 | 95.8 | 75.9 | 80.4 | 34.5 | 69.9 |
| - assemble off | 71.3 | +0.0 | 95.8 | 75.9 | 80.4 | 34.5 | 69.9 |
| - shape off | 58.6 | -12.7 | 30.6 | 75.9 | 81.3 | 35.2 | 69.9 |
| + read.layout_furniture on | 71.0 | -0.3 | 94.4 | 75.9 | 80.4 | 34.5 | 69.9 |
| + read.layout_furniture on, edge band 0.12 | 71.0 | -0.3 | 94.4 | 75.9 | 80.4 | 34.5 | 69.9 |
| - read.segment off (page-level) | 71.3 | +0.0 | 95.8 | 75.9 | 80.4 | 34.5 | 69.9 |
| + verify.fusion on | 71.3 | +0.0 | 95.8 | 75.9 | 80.4 | 34.5 | 69.9 |
| - read.robust off (no retry/trim) | 71.3 | +0.0 | 95.8 | 75.9 | 80.4 | 34.5 | 69.9 |

Side metrics of the full pipeline (not part of the score):

- pages: 140
- errors: 0
- citation_coverage_digital: 92.9
- n_digital: 77
- citation_coverage_scanned: 81.8
- n_scanned: 63
- pages_with_text_layer: 94
- pages_text_layer_usable: 56
- regions: 3391
- regions_with_box_pct: 91.9
- regions_low_confidence_pct: 34.3

## quality (reader `cached:haiku_hires`, layout `cached:pvl`, words `cached:ppocr`)

| config | score | delta | headers | tiny | multicol | scans | tables |
|---|---|---|---|---|---|---|---|
| raw model output | 81.6 | -2.7 | 93.1 | 94.6 | 89.7 | 43.7 | 87.0 |
| full pipeline | 84.3 | 0.0 | 95.8 | 94.6 | 91.6 | 45.8 | 93.5 |
| - triage off | 82.3 | -2.0 | 95.8 | 94.6 | 89.7 | 44.4 | 87.0 |
| - layout off | 82.4 | -1.9 | 93.1 | 94.6 | 91.6 | 45.8 | 87.0 |
| - read off | 21.3 | -63.0 | 97.2 | 0.0 | 0.0 | 9.2 | 0.0 |
| - verify off | 84.3 | +0.0 | 95.8 | 94.6 | 91.6 | 45.8 | 93.5 |
| - ground off | 84.3 | +0.0 | 95.8 | 94.6 | 91.6 | 45.8 | 93.5 |
| - assemble off | 84.3 | +0.0 | 95.8 | 94.6 | 91.6 | 45.8 | 93.5 |
| - shape off | 71.2 | -13.1 | 33.3 | 94.6 | 91.6 | 43.0 | 93.5 |
| + read.layout_furniture on | 84.1 | -0.2 | 97.2 | 94.6 | 90.7 | 44.4 | 93.5 |
| + read.layout_furniture on, edge band 0.12 | 84.7 | +0.4 | 97.2 | 94.6 | 91.6 | 46.5 | 93.5 |
| - read.segment off (page-level) | 82.3 | -2.0 | 93.1 | 94.6 | 91.6 | 45.1 | 87.0 |
| - read.select off (first read always) | 83.6 | -0.7 | 95.8 | 94.6 | 89.7 | 44.4 | 93.5 |
| - read.furniture_consensus off | 83.7 | -0.6 | 93.1 | 94.6 | 91.6 | 45.8 | 93.5 |
| - read.table_select off | 83.0 | -1.3 | 95.8 | 94.6 | 91.6 | 45.8 | 87.0 |
| - read.table_reader off (no crop re-read) | 83.3 | -1.0 | 95.8 | 94.6 | 91.6 | 45.8 | 88.6 |
| - read.candidates off (one read) | 81.8 | -2.5 | 93.1 | 94.6 | 89.7 | 44.4 | 87.0 |
| + verify.fusion on | 84.3 | +0.0 | 95.8 | 94.6 | 91.6 | 45.8 | 93.5 |
| - read.robust off (no retry/trim) | 84.3 | +0.0 | 95.8 | 94.6 | 91.6 | 45.8 | 93.5 |

Side metrics of the full pipeline (not part of the score):

- pages: 140
- errors: 0
- citation_coverage_digital: 92.7
- n_digital: 77
- citation_coverage_scanned: 81.7
- n_scanned: 63
- pages_with_text_layer: 94
- pages_text_layer_usable: 56
- regions: 2337
- regions_with_box_pct: 93.1
- regions_low_confidence_pct: 20.2
- est_cost_usd_per_page: 0.01622
- page_escalations: 0
- page_escalation_rate_pct: 0.0
- region_escalations: 18
- escalated_page_equivalents: 3.8

## cheap (reader `cached:glm_pdf`, layout `cached:pvl`, words `cached:ppocr`)

| config | score | delta | headers | tiny | multicol | scans | tables |
|---|---|---|---|---|---|---|---|
| raw model output | 71.3 | -7.2 | 95.8 | 75.9 | 80.4 | 34.5 | 69.9 |
| full pipeline | 78.5 | 0.0 | 97.2 | 90.6 | 81.3 | 34.5 | 88.6 |
| - triage off | 77.6 | -0.8 | 97.2 | 90.6 | 81.3 | 34.5 | 84.6 |
| - layout off | 71.3 | -7.2 | 95.8 | 75.9 | 80.4 | 34.5 | 69.9 |
| - read off | 21.3 | -57.2 | 97.2 | 0.0 | 0.0 | 9.2 | 0.0 |
| - verify off | 78.5 | +0.0 | 97.2 | 90.6 | 81.3 | 34.5 | 88.6 |
| - ground off | 78.5 | +0.0 | 97.2 | 90.6 | 81.3 | 34.5 | 88.6 |
| - assemble off | 78.5 | +0.0 | 97.2 | 90.6 | 81.3 | 34.5 | 88.6 |
| - shape off | 65.5 | -13.0 | 30.6 | 90.6 | 82.2 | 35.2 | 88.6 |
| + read.layout_furniture on | 78.2 | -0.3 | 95.8 | 90.6 | 81.3 | 34.5 | 88.6 |
| + read.layout_furniture on, edge band 0.12 | 78.2 | -0.3 | 95.8 | 90.6 | 81.3 | 34.5 | 88.6 |
| - read.segment off (page-level) | 74.4 | -4.0 | 95.8 | 90.6 | 81.3 | 34.5 | 69.9 |
| - escalate pages off | 75.0 | -3.4 | 95.8 | 75.9 | 80.4 | 34.5 | 88.6 |
| - escalate tables off | 74.7 | -3.7 | 97.2 | 90.6 | 81.3 | 34.5 | 69.9 |
| - escalation off (fast tier only) | 71.3 | -7.2 | 95.8 | 75.9 | 80.4 | 34.5 | 69.9 |
| - verify.fusion off | 78.5 | +0.0 | 97.2 | 90.6 | 81.3 | 34.5 | 88.6 |
| + verify.fusion_second_read on (substitute on second read + OCR) | 78.5 | +0.1 | 97.2 | 91.1 | 81.3 | 34.5 | 88.6 |
| - read.robust off (no retry/trim) | 78.5 | +0.0 | 97.2 | 90.6 | 81.3 | 34.5 | 88.6 |

Side metrics of the full pipeline (not part of the score):

- pages: 140
- errors: 0
- citation_coverage_digital: 92.8
- n_digital: 77
- citation_coverage_scanned: 82.0
- n_scanned: 63
- pages_with_text_layer: 94
- pages_text_layer_usable: 56
- regions: 3192
- regions_with_box_pct: 91.7
- regions_low_confidence_pct: 30.9
- est_cost_usd_per_page: 0.00135
- page_escalations: 14
- page_escalation_rate_pct: 10.0
- region_escalations: 27
- escalated_page_equivalents: 20.2

Reading the table: a negative delta on a `- <stage> off` row means that stage helps; 0.0 means the stage does not touch the scored text (verify, ground and assemble only add confidence, boxes and structure at this point). `+` rows are optional switches that are off by default; they stay off unless they raise the score on every profile. `read off` still scores on headers_footers because absence tests pass on empty output. Rows after `read.segment` are workstream A's mechanisms (selection between reads, furniture by consensus, table verification and crop re-reads, escalation, token fusion, retry policy); `est_cost_usd_per_page` uses the cost model fast $0.0002/page, Haiku $0.008/page, crops by area. `shape off` drops on headers_footers because the bench then scores the serializer's unfiltered default chunk, which includes the Header/Footer/Page Number blocks taken from the layout detector.
