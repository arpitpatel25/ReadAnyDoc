"""Parse a document and print it as Markdown, with what it cost.

    python examples/parse.py invoice.pdf
"""

import sys

import readanydoc

result = readanydoc.parse(sys.argv[1])

print(result.markdown)
print(f"\n{result.pages} pages, {len(result.blocks)} blocks, {len(result.tables)} tables")
print(f"{result.usage.calls} model calls, ${result.usage.cost_usd or 0:.4f}, {result.usage.seconds:.0f} s")
for warning in result.warnings:
    print("warning:", warning)
