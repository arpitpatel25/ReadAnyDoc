"""Put a small logo in front of well-known names (Reducto, Claude, OpenAI, GitHub, ...) in the built website and docs.

Runs on the assembled site in the Pages workflow, so the source HTML and Markdown stay plain:

    python scripts/brand_logos.py _site

Text inside code, scripts, SVG, the docs navigation and the search box is left alone. A name gets its logo once per
section of the landing page and once per page elsewhere (docs, build story), in running text only, never in headings.
Logos live in site/logos/ (Simple Icons, CC0, plus each project's own mark).
"""

import html
import re
import sys
from pathlib import Path

BASE = "/ReadAnyDoc/logos/"

# Longest names first; each maps to a logo file in site/logos/. Black logos are marked so dark backgrounds invert them.
BRANDS = [
    ("Claude Code", "claude.svg"), ("Claude", "claude.svg"), ("Anthropic", "anthropic.svg"),
    ("OpenAI", "openai.svg"), ("Codex", "openai.svg"), ("vLLM", "vllm.png"),
    ("Reducto", "reducto.svg"), ("Docker", "docker.svg"), ("GitHub", "github.svg"),
    ("Python", "python.svg"), ("PyPI", "pypi.svg"), ("Mistral", "mistralai.svg"),
    ("LlamaParse", "llamaindex.png"), ("LlamaIndex", "llamaindex.png"), ("Hugging Face", "huggingface.svg"),
    ("Ollama", "ollama.svg"), ("Cursor", "cursor.svg"), ("Windsurf", "windsurf.svg"), ("LangChain", "langchain.svg"),
    ("FastAPI", "fastapi.svg"), ("Kubernetes", "kubernetes.svg"), ("Gemini", "googlegemini.svg"),
    ("MCP", "modelcontextprotocol.svg"), ("AWS", "amazonwebservices.svg"), ("Textract", "amazonwebservices.svg"),
    ("Azure", "microsoftazure.svg"), ("olmOCR", "ai2.png"), ("Docling", "docling.png"),
    ("Unstructured", "unstructured.png"),
]
MONO = {"openai.svg", "github.svg", "anthropic.svg", "ollama.svg", "cursor.svg", "windsurf.svg",
        "modelcontextprotocol.svg", "amazonwebservices.svg", "langchain.svg"}
LOGO = dict(BRANDS)
NAME_RE = re.compile(r"(?<![\w./-])(" + "|".join(re.escape(n) for n, _ in BRANDS) + r")(?![\w-]*\.(?:com|ai|io|org)\b)(?!\w)((?:['’]s|-[A-Za-z][\w-]*)?)")

SKIP_TAGS = {"nav", "h1", "h2", "h3", "h4", "h5", "h6", "code", "pre", "script", "style", "title", "head", "svg", "textarea", "option", "select", "kbd", "samp"}
SKIP_CLASS = re.compile(r"\b(md-nav|md-search|md-header|md-source|md-tabs|headerlink|md-footer__link|menu|nav-links|no-logos)\b")
CONTAINERS = {"section", "header", "footer", "article", "nav"}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

STYLE = ("<style id=\"brand-logos\">.bl{white-space:nowrap}.bl img{height:.95em;width:auto;vertical-align:-.13em;"
         "margin-right:.3em;border-radius:2px;display:inline}"
         "[data-md-color-scheme=slate] .bl-mono img{filter:invert(1)}</style>")

TOKEN = re.compile(r"<!--.*?-->|<![^>]*>|<[^>]+>|[^<]+", re.S)
TAG = re.compile(r"<(/?)([a-zA-Z][\w-]*)([^>]*?)(/?)>", re.S)


def logo(name, tail=""):
    f = LOGO[name]
    cls = "bl bl-mono" if f in MONO else "bl"
    return (f'<span class="{cls}"><img src="{BASE}{f}" alt="" width="16" height="16" loading="lazy" '
            f'decoding="async">{name}{tail}</span>')


def transform(text, per_section=True):
    if 'id="brand-logos"' in text:
        return text  # already done
    out, stack, seen = [], [], set()
    for tok in TOKEN.findall(text):
        m = TAG.fullmatch(tok) if tok.startswith("<") else None
        if m:
            closing, name, attrs, selfclose = m.group(1), m.group(2).lower(), m.group(3), m.group(4)
            if closing:
                while stack:
                    top = stack.pop()
                    if top[0] == name:
                        break
            elif not selfclose and name not in VOID:
                skip = name in SKIP_TAGS or bool(SKIP_CLASS.search(attrs))
                stack.append((name, skip))
            if per_section and name in CONTAINERS and not closing:
                seen = set()
            out.append(tok)
            continue
        if tok.startswith("<") or any(s for _, s in stack):
            out.append(tok)
            continue

        def sub(mm):
            n, tail = mm.group(1), mm.group(2)
            if LOGO[n] in seen:
                return mm.group(0)
            seen.add(LOGO[n])
            return logo(n, tail)

        out.append(NAME_RE.sub(sub, tok))
    result = "".join(out)
    return result.replace("</head>", STYLE + "</head>", 1)


def main(root):
    n = 0
    for p in Path(root).rglob("*.html"):
        if p.name.startswith("google"):
            continue
        t = p.read_text(encoding="utf-8")
        new = transform(t, per_section=(p.parent == Path(root)))  # landing page: once per section; others: once per page
        if new != t:
            p.write_text(new, encoding="utf-8")
            n += 1
    print(f"brand logos added to {n} pages")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "_site")
