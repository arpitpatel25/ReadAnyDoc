"""Score pipeline configs and write the stage on/off ablation table.

    uv run python -m readanydoc_research bench --systems haiku glm_pdf pvl16 reducto_r1 reducto_r1mini
    uv run python -m readanydoc_research bench --profile cached-haiku            # 30-page dev subset
    uv run python -m readanydoc_research bench --profile cached-haiku --full     # all 140 pages
    uv run python -m readanydoc_research bench --ablations --full                # rewrites research/results/ABLATIONS.md
"""

from __future__ import annotations

import datetime
import statistics
from typing import Optional

from readanydoc import paths
from readanydoc.model import Document
from readanydoc.pipeline import STAGE_ORDER, Pipeline, PipelineConfig
from readanydoc.profiles import get_profile
from readanydoc.serialize import document_markdown
from . import olm

# Same filter the spike applied to Reducto's own output (spike/conv_reducto.py).
BENCH_FILTER = ["Header", "Footer", "Page Number"]
ABLATIONS_FILE = paths.REPO_ROOT / "research" / "results" / "ABLATIONS.md"


def bench_config(profile: str | PipelineConfig) -> PipelineConfig:
    cfg = get_profile(profile) if isinstance(profile, str) else profile.model_copy(deep=True)
    cfg.parse.filter_blocks = list(BENCH_FILTER)
    return cfg


class PipelineRunner:
    """Runs one config over bench pages, keeping the Documents for the side metrics."""

    def __init__(self, config: PipelineConfig):
        self.pipeline = Pipeline(config)
        self.docs: dict[str, Document] = {}
        self.errors: dict[str, str] = {}

    def markdown(self, stem: str) -> Optional[str]:
        try:
            doc = self.pipeline.run(stem)
        except Exception as e:
            self.errors[stem] = f"{type(e).__name__}: {e}"
            return None
        self.docs[stem] = doc
        return document_markdown(doc)

    def side_metrics(self) -> dict[str, float | int | None]:
        cov = {"digital": [], "scanned": []}
        usable = with_layer = low = regions = boxed = 0
        for doc in self.docs.values():
            for page in doc.pages:
                c = page.properties.get("ground_coverage")
                if c is not None and page.kind in cov:
                    cov[page.kind].append(c)
                with_layer += bool(page.properties.get("has_text_layer"))
                usable += bool(page.text_layer_usable)
                for r in page.regions:
                    regions += 1
                    boxed += r.bbox is not None
                    low += r.confidence.label == "low"
        mean = lambda xs: round(100 * statistics.mean(xs), 1) if xs else None
        cost = self.cost_metrics()
        return {**self._base_metrics(cov, with_layer, usable, low, regions, boxed, mean), **cost}

    def cost_metrics(self) -> dict[str, float | int | None]:
        """Workstream A: estimated $/page and escalation rate (cost model in reading/escalate.py)."""
        costs = [d.meta["cost"] for d in self.docs.values() if d.meta.get("cost")]
        n = sum(len(d.pages) for d in self.docs.values())
        if not costs or not n:
            return {}
        pages = sum(c["page_escalations"] for c in costs)
        regions = sum(c["region_escalations"] for c in costs)
        return {"est_cost_usd_per_page": round(sum(c["est_cost_usd"] for c in costs) / n, 5),
                "page_escalations": pages, "page_escalation_rate_pct": round(100 * pages / n, 1),
                "region_escalations": regions,
                "escalated_page_equivalents": round(sum(c["escalated_area"] for c in costs), 2)}

    def _base_metrics(self, cov, with_layer, usable, low, regions, boxed, mean) -> dict[str, float | int | None]:
        return {"pages": len(self.docs), "errors": len(self.errors),
                "citation_coverage_digital": mean(cov["digital"]), "n_digital": len(cov["digital"]),
                "citation_coverage_scanned": mean(cov["scanned"]), "n_scanned": len(cov["scanned"]),
                "pages_with_text_layer": with_layer, "pages_text_layer_usable": usable,
                "regions": regions, "regions_with_box_pct": round(100 * boxed / regions, 1) if regions else None,
                "regions_low_confidence_pct": round(100 * low / regions, 1) if regions else None}


def score_pipeline(name: str, config: PipelineConfig, stems: Optional[list[str]] = None):
    runner = PipelineRunner(config)
    result = olm.score(name, runner.markdown, stems)
    return result, runner


def ablation_variants(base: PipelineConfig) -> list[tuple[str, PipelineConfig]]:
    out = [("full pipeline", base)]
    for stage in STAGE_ORDER:
        if stage == "intake":
            continue  # required: nothing runs without pages
        out.append((f"- {stage} off", base.with_stage(stage, enabled=False)))
    # candidate switches that ship OFF because they do not raise the score on both profiles
    out.append(("+ read.layout_furniture on", base.with_stage("read", layout_furniture=True)))
    out.append(("+ read.layout_furniture on, edge band 0.12",
                base.with_stage("read", layout_furniture=True, furniture_band=0.12)))
    out.append(("- read.segment off (page-level)", base.with_stage("read", segment=False)))
    # workstream A switches: each row removes (or adds) one mechanism of the profile
    ropt = base.stage("read").options
    fusion_on = bool(base.stage("verify").options.get("fusion", False))
    if ropt.get("candidates"):
        out.append(("- read.select off (first read always)", base.with_stage("read", select=False)))
        out.append(("- read.furniture_consensus off", base.with_stage("read", furniture_consensus=False)))
        out.append(("- read.table_select off", base.with_stage("read", table_select=False)))
        if ropt.get("table_reader"):
            out.append(("- read.table_reader off (no crop re-read)", base.with_stage("read", table_reader=None)))
        out.append(("- read.candidates off (one read)", base.with_stage("read", candidates=[])))
    if ropt.get("escalate"):
        esc = dict(ropt["escalate"])
        out.append(("- escalate pages off", base.with_stage("read", escalate={**esc, "pages": False})))
        out.append(("- escalate tables off", base.with_stage("read", escalate={**esc, "tables": False})))
        out.append(("- escalation off (fast tier only)", base.with_stage("read", escalate=None)))
    out.append((f"{'-' if fusion_on else '+'} verify.fusion {'off' if fusion_on else 'on'}",
                base.with_stage("verify", fusion=not fusion_on)))
    if fusion_on:
        out.append(("+ verify.fusion_second_read on (substitute on second read + OCR)",
                    base.with_stage("verify", fusion_second_read=True)))
    out.append(("- read.robust off (no retry/trim)", base.with_stage("read", robust=False)))
    return out


def run_ablations(profiles: list[str], full: bool = False, write: bool = True) -> str:
    stems = None if full else olm.dev_subset()
    scope = "all 140 pages" if full else "30-page dev subset (bench/dev30.json)"
    cats = "| headers | tiny | multicol | scans | tables |"
    lines = ["# Ablations: stage on/off -> 5-category olmOCR-bench score", "",
             f"Generated {datetime.date.today().isoformat()} by `uv run python -m readanydoc_research bench --ablations"
             f"{' --full' if full else ''}`. Scope: {scope}.",
             "Score = mean of the 5 category pass rates (official olmOCR-bench tests). "
             "Delta is against the full pipeline of the same profile.",
             "Bench output = serializer chunk content with `filter_blocks=[Header, Footer, Page Number]`.", ""]
    for profile in profiles:
        base = bench_config(profile)
        raw_system = base.reader.split(":", 1)[1]
        raw = olm.score("raw model output", olm.cached_markdown(raw_system), stems)
        lines += [f"## {profile} (reader `{base.reader}`, layout `{base.layout}`, words `{base.words}`)", "",
                  f"| config | score | delta {cats}", "|---|---|---|---|---|---|---|---|"]
        fmt = lambda r, d: (f"| {r.name} | {r.mean:.1f} | {d} | "
                            + " | ".join(f"{r.rate(c):.1f}" for c in olm.CATS) + " |")
        print(f"\n== {profile} ({scope})\n{olm.HEADER}\n{raw.row()}")
        full_res = None
        metrics = None
        rows = []
        for name, cfg in ablation_variants(base):
            res, runner = score_pipeline(name, cfg, stems)
            print(res.row() + (f"   errors={len(runner.errors)}" if runner.errors else ""))
            if full_res is None:
                full_res, metrics = res, runner.side_metrics()
                rows.append(fmt(res, "0.0"))
            else:
                rows.append(fmt(res, f"{res.mean - full_res.mean:+.1f}"))
        lines.append(fmt(raw, f"{raw.mean - full_res.mean:+.1f}"))
        lines += rows
        lines += ["", "Side metrics of the full pipeline (not part of the score):", ""]
        lines += [f"- {k}: {v}" for k, v in (metrics or {}).items()]
        lines.append("")
    lines += ["Reading the table: a negative delta on a `- <stage> off` row means that stage helps; "
              "0.0 means the stage does not touch the scored text (verify, ground and assemble only add "
              "confidence, boxes and structure at this point). `+` rows are optional switches that are "
              "off by default; they stay off unless they raise the score on every profile. "
              "`read off` still scores on headers_footers because absence tests pass on empty output. "
              "Rows after `read.segment` are workstream A's mechanisms (selection between reads, furniture by "
              "consensus, table verification and crop re-reads, escalation, token fusion, retry policy); "
              "`est_cost_usd_per_page` uses the cost model fast $0.0002/page, Haiku $0.008/page, crops by area. "
              "`shape off` drops on headers_footers because the bench then scores the serializer's unfiltered default chunk, which includes the Header/Footer/Page Number blocks taken from the layout detector.", ""]
    text = "\n".join(lines)
    if write:
        ABLATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        ABLATIONS_FILE.write_text(text)
    return text
