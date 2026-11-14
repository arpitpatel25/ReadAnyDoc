"""Deterministic block typing. No model call.

``markup``: what the text of a block says about its type (headings, figure notes, key/value lines,
list items), used by ``mdblocks.split_markdown`` before anything is located on the page.
``layout``: what the position of a located block says (header, footer, page number, title), used by
the Ground stage once the boxes exist.
Conventions follow Reducto r-1's real responses on 147 business pages (see ``e2e/``).
"""

from .layout import retype_page
from .markup import classify_text, is_key_value, split_list_items

__all__ = ["classify_text", "is_key_value", "retype_page", "split_list_items"]
