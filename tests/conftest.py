import os

import pytest

# the server's janitor must never sweep the shared working cache from a test run
os.environ.setdefault("READANYDOC_CLEAN_CACHE", "0")

from readanydoc import paths
import readanydoc_research.profiles  # noqa: F401  (registers the development profiles the tests use)

HAVE_SPIKE = (paths.spike_dir() / "out" / "haiku").is_dir()
needs_spike = pytest.mark.skipif(not HAVE_SPIKE, reason="cached spike artifacts not available")

# one page per category, all in the fixed dev subset
PAGES = {
    "multi_column": "0097571b436308f563a5808d45d0232255a2_page_6_pg1",
}


@pytest.fixture(scope="session")
def five_pages():
    from readanydoc_research.bench import olm
    dev = set(olm.dev_subset())
    return [next(s for s in olm.pages_by_cat()[c] if s in dev) for c in olm.CATS]


@pytest.fixture(scope="session")
def haiku_docs(five_pages):
    from readanydoc.pipeline import Pipeline
    from readanydoc.profiles import get_profile
    pipe = Pipeline(get_profile("cached-haiku"))
    return {s: pipe.run(s) for s in five_pages}
