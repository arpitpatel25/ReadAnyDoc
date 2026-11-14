"""Use Reducto's own Python SDK against a ReadAnyDoc server: only the base URL changes.

    readanydoc serve --port 8000          # in another terminal
    pip install reductoai
    python examples/reducto_sdk.py invoice.pdf
"""

import sys
from pathlib import Path

from reducto import Reducto

client = Reducto(api_key="not-checked-unless-READANYDOC_API_KEY-is-set", base_url="http://127.0.0.1:8000")

upload = client.upload(file=Path(sys.argv[1]))
parsed = client.parse.run(input=upload.file_id)
for chunk in parsed.result.chunks:
    print(chunk.content)
