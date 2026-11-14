"""Parse Judge comparative prompt, byte-for-byte (Reducto's open judge, Apache-2.0).

Source: https://github.com/reductoai/parsejudge  packages/core/src/prompt.ts and judge.ts
at commit SOURCE_COMMIT (local copy in ``benchmarks/parsejudge``). ``prompt_template.txt`` is generated
from prompt.ts by ``sync_prompt.py``; never edit it by hand. A changed prompt means a new JUDGE_VERSION.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

JUDGE_VERSION = "compare-v4"
SOURCE_COMMIT = "14e041781bf830b5697ccb09e2f64b833f45a536"
SOURCE_REPO = "https://github.com/reductoai/parsejudge"
# sha256 of prompt_template.txt as generated at SOURCE_COMMIT; a test pins it
TEMPLATE_SHA256 = "464db54dcbc76d69ac09d84926d0c9953140a29bdd7f65a7452cdc936a91a1d6"

SEVERITIES = ("high", "low", "figure_error")
CATEGORIES = (
    "content_omission",
    "content_hallucination",
    "text_corruption",
    "generation_repetition_loop",
    "reading_order",
    "table_structure",
    "table_content",
    "field_association_error",
    "checkbox_state_error",
    "watermark",
    "figure_precision",
    "figure_recall",
    "other",
)


@lru_cache(maxsize=1)
def template() -> str:
    return (Path(__file__).resolve().parent / "prompt_template.txt").read_text(encoding="utf-8")


def template_sha256() -> str:
    return hashlib.sha256(template().encode("utf-8")).hexdigest()


def compare_prompt(parse_sections: str, labels: str) -> str:
    """``comparePrompt(parseSections, labels)`` of prompt.ts. Single pass, so a parse that happens to
    contain the text ``${labels}`` is left alone, as in a JavaScript template literal."""
    head, rest = template().split("${parseSections}", 1)
    middle, tail = rest.split("${labels}", 1)
    return head + parse_sections + middle + labels + tail


def parse_sections(labels: list[str], markdown_by_label: dict[str, str]) -> str:
    """Same layout as judge.ts: ``## parse_a`` then the parse inside a four-backtick ``text`` fence."""
    return "\n\n".join(f"## {label}\n\n````text\n{markdown_by_label.get(label, '')}\n````" for label in labels)


def response_schema(labels: list[str]) -> dict:
    """The strict JSON schema judge.ts sends as ``response_format`` (``responseFormat(labels)``)."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["page_features", "evaluations"],
        "properties": {
            "page_features": {"type": "array", "items": {"type": "string"}},
            "evaluations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["label", "errors", "reasoning", "score"],
                    "properties": {
                        "label": {"type": "string", "enum": list(labels)},
                        "errors": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["severity", "category", "visual_region_type", "location_hint",
                                             "description"],
                                "properties": {
                                    "severity": {"type": "string", "enum": list(SEVERITIES)},
                                    "category": {"type": "string", "enum": list(CATEGORIES)},
                                    "visual_region_type": {"type": "string"},
                                    "location_hint": {"type": "string"},
                                    "description": {"type": "string"},
                                },
                            },
                        },
                        "reasoning": {"type": "string"},
                        "score": {"type": "integer", "minimum": 1, "maximum": 10},
                    },
                },
            },
        },
    }
