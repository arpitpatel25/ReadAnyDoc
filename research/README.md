# research

Code and notes used to develop and measure ReadAnyDoc. Nothing here is installed with the package.

- `readanydoc_research/profiles.py`: experimental pipeline profiles (cached readers, ablations, earlier engine
  versions). They are registered only when this module is imported or listed in `READANYDOC_PROFILE_MODULES`, and they
  can never replace the shipped `engine` and `model-only` profiles.
- `readanydoc_research/bench/`: olmOCR-bench sample scoring, stage ablations, the grounding report and the LLM-judge
  comparison used early in development.
- `results/`: the fixed 30-page development sample (`dev30.json`) and the ablation table.
- `benchmarks/`: the scripts that ran and scored the final comparison ([BENCHMARKS.md](../BENCHMARKS.md)).

The story of the research, stage by stage, is in [docs/research/](../docs/research/).

```bash
# needs the cached model outputs and benchmark pages (READANYDOC_SPIKE_DIR); not distributed with the repo
uv run python -m readanydoc_research bench --profile cached-haiku            # 30-page development sample
uv run python -m readanydoc_research bench --profile cached-haiku --full     # all 140 pages
uv run python -m readanydoc_research bench --ablations --full

# use a research profile in the server or library
READANYDOC_PROFILE_MODULES=readanydoc_research.profiles READANYDOC_PROFILE=engine-v2 uv run readanydoc serve
```
