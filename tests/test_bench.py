import json

import pytest

from .conftest import needs_spike

BASELINES = {"haiku": 80.4, "glm_pdf": 71.3, "pvl16": 70.4, "reducto_r1": 75.9, "reducto_r1mini": 73.9}
PER_CAT = {"haiku": [92, 84, 89, 45, 93], "reducto_r1": [93, 88, 69, 42, 88]}


@needs_spike
def test_bench_has_140_pages_28_per_category():
    from readanydoc_research.bench import olm
    pages = olm.pages_by_cat()
    assert [len(pages[c]) for c in olm.CATS] == [28] * 5 and len(set(olm.all_stems())) == 140
    assert sum(len(t) for t in olm.tests_by_cat().values()) == 668


@needs_spike
def test_dev_subset_is_fixed():
    from readanydoc_research.bench import olm
    dev = olm.dev_subset()
    assert len(dev) == len(set(dev)) == 30
    saved = json.loads(olm.DEV_FILE.read_text())
    assert saved["seed"] == olm.DEV_SEED
    for c in olm.CATS:
        assert len(saved["pages"][c]) == 6 and set(saved["pages"][c]) <= set(olm.pages_by_cat()[c])
    assert dev == olm.dev_subset()


@needs_spike
@pytest.mark.parametrize("system,expected", BASELINES.items())
def test_bench_reproduces_known_baselines(system, expected):
    from readanydoc_research.bench import olm
    res = olm.score(system, olm.cached_markdown(system))
    assert res.missing == 0
    assert round(res.mean, 1) == expected
    if system in PER_CAT:
        assert [round(res.rate(c)) for c in olm.CATS] == PER_CAT[system]


@needs_spike
@pytest.mark.parametrize("profile,system", [("cached-haiku", "haiku"), ("cached-glm", "glm_pdf")])
def test_pipeline_is_not_worse_than_raw_model_on_dev_subset(profile, system):
    from readanydoc_research.bench import olm, run
    dev = olm.dev_subset()
    raw = olm.score("raw", olm.cached_markdown(system), dev)
    res, runner = run.score_pipeline("pipeline", run.bench_config(profile), dev)
    assert not runner.errors
    assert res.mean >= raw.mean - 1e-9
    m = runner.side_metrics()
    assert m["pages"] == 30 and m["regions_with_box_pct"] > 80
