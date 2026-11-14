"""Choose the mode and every model the pipeline uses.

Only Claude Haiku 5.5 (with Sonnet 5.5 for handwriting) is benchmarked; other models work but are unmeasured.

    python examples/choose_models.py scan.pdf
"""

import sys

import readanydoc

path = sys.argv[1]

# One model read per page, nothing else: cheapest and fastest.
quick = readanydoc.parse(path, mode="model-only")

# The engine (default) with a stronger page reader and extra request options passed to the provider as given.
careful = readanydoc.parse(
    path,
    model="claude-sonnet-5-5", effort="high",
    model_options={"max_tokens": 32000},
    handwriting_model="claude-sonnet-5-5",
)

# OpenAI, or any OpenAI-compatible server (for example vLLM on your own machine).
# readanydoc.parse(path, model="gpt-5")                                       # needs OPENAI_API_KEY
# readanydoc.parse(path, model="openai_compatible:my-model@http://localhost:8001/v1")

for name, result in [("model-only", quick), ("engine (Sonnet)", careful)]:
    print(f"{name}: {len(result.blocks)} blocks, ${result.usage.cost_usd or 0:.4f}")
