"""Reducto's Parse Judge (PAIR), ported: blind comparative LLM judging of parser outputs per page.

    uv run python -m readanydoc_research bench --judge --pages <dir or list> --systems a b c --baseline reducto_r1
"""

from .core import (DEFAULT_JUDGE_MODEL, BudgetExceeded, Judge, JudgeError, JudgeRequest, PageJudgment, Verdict,
                   blind_labels, claude_cli_judge, extract_json, unblind, validate)
from .prompt import JUDGE_VERSION, SOURCE_COMMIT, compare_prompt
from .summary import PageResult, format_summary, summarize

__all__ = ["DEFAULT_JUDGE_MODEL", "BudgetExceeded", "Judge", "JudgeError", "JudgeRequest", "PageJudgment",
           "Verdict", "blind_labels", "claude_cli_judge", "extract_json", "unblind", "validate", "JUDGE_VERSION",
           "SOURCE_COMMIT", "compare_prompt", "PageResult", "format_summary", "summarize"]
