"""HTML -> Markdown conversion for the authority dashboard.

This module converts SO body HTML to Markdown so Streamlit's ``st.markdown`` renders code blocks properly with syntax highlighting. 
"""

from __future__ import annotations

import html
import re

try:
    from bs4 import BeautifulSoup, NavigableString
    _HAVE_BS4 = True
except ImportError:  # pragma: no cover
    _HAVE_BS4 = False


# --------------------------------------------------------------------------
# Markdown conversion
# --------------------------------------------------------------------------
def html_to_markdown(body_html: str | None) -> str:
    """Convert an SO body HTML string to Markdown.

    Preserves:
      - <pre><code> -> fenced ``` blocks (language guessed from class)
      - inline <code> -> backticks
      - <a href> -> [text](url)
      - <strong>/<b>/<em>/<i> -> **/*
      - <ul>/<ol>/<li> -> markdown lists
      - <blockquote> -> > prefix
      - <br>, <p> -> newlines

    Everything else degrades to its text content.
    """
    if not body_html:
        return ""
    if _HAVE_BS4:
        return _bs4_to_md(body_html)
    return _stdlib_to_md(body_html)


# --------------------------------------------------------------------------
# bs4 implementation (preferred)
# --------------------------------------------------------------------------
def _bs4_to_md(body_html: str) -> str:
    soup = BeautifulSoup(body_html, "html.parser")
    return _render(soup).strip()


def _render(node) -> str:
    if isinstance(node, NavigableString):
        return str(node)
    if node.name is None:  # the root document
        return "".join(_render(c) for c in node.children)

    tag = node.name.lower()

    if tag == "pre":
        # SO wraps code in <pre><code class="language-foo">
        code_el = node.find("code")
        text = (code_el.get_text() if code_el else node.get_text())
        lang = ""
        if code_el and code_el.get("class"):
            for cls in code_el.get("class"):
                if cls.startswith("language-"):
                    lang = cls.removeprefix("language-")
                    break
                if cls.startswith("lang-"):
                    lang = cls.removeprefix("lang-")
                    break
        text = text.rstrip("\n")
        return f"\n```{lang}\n{text}\n```\n"

    if tag == "code":
        # inline code only (block code already handled by <pre> branch)
        return f"`{node.get_text()}`"

    if tag == "a":
        text = "".join(_render(c) for c in node.children).strip()
        href = node.get("href", "")
        return f"[{text}]({href})" if href else text

    if tag in ("strong", "b"):
        return f"**{''.join(_render(c) for c in node.children)}**"

    if tag in ("em", "i"):
        return f"*{''.join(_render(c) for c in node.children)}*"

    if tag == "br":
        return "  \n"

    if tag == "p":
        inner = "".join(_render(c) for c in node.children)
        return f"\n{inner}\n"

    if tag == "blockquote":
        inner = "".join(_render(c) for c in node.children).strip()
        return "\n" + "\n".join(f"> {line}" for line in inner.splitlines()) + "\n"

    if tag == "ul":
        items = [
            f"- {''.join(_render(c) for c in li.children).strip()}"
            for li in node.find_all("li", recursive=False)
        ]
        return "\n" + "\n".join(items) + "\n"

    if tag == "ol":
        items = [
            f"{i+1}. {''.join(_render(c) for c in li.children).strip()}"
            for i, li in enumerate(node.find_all("li", recursive=False))
        ]
        return "\n" + "\n".join(items) + "\n"

    if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
        level = int(tag[1])
        inner = "".join(_render(c) for c in node.children).strip()
        return f"\n{'#' * level} {inner}\n"

    if tag in ("hr",):
        return "\n---\n"

    # default: pass through children
    return "".join(_render(c) for c in node.children)


# --------------------------------------------------------------------------
# Stdlib fallback (regex-based; only used if bs4 missing)
# --------------------------------------------------------------------------
_RX_PRE_CODE = re.compile(
    r"<pre[^>]*>\s*<code(?P<attrs>[^>]*)>(?P<body>.*?)</code>\s*</pre>",
    re.DOTALL | re.IGNORECASE,
)
_RX_INLINE_CODE = re.compile(r"<code[^>]*>(.*?)</code>", re.DOTALL | re.IGNORECASE)
_RX_TAG = re.compile(r"<[^>]+>")


def _stdlib_to_md(body_html: str) -> str:
    def pre_repl(m: re.Match) -> str:
        lang = ""
        cls_match = re.search(r'class="([^"]+)"', m.group("attrs") or "")
        if cls_match:
            for token in cls_match.group(1).split():
                if token.startswith("language-"):
                    lang = token.removeprefix("language-")
                    break
                if token.startswith("lang-"):
                    lang = token.removeprefix("lang-")
                    break
        body = html.unescape(m.group("body")).rstrip("\n")
        return f"\n```{lang}\n{body}\n```\n"

    s = _RX_PRE_CODE.sub(pre_repl, body_html)
    s = _RX_INLINE_CODE.sub(lambda m: f"`{html.unescape(m.group(1))}`", s)
    s = s.replace("<br>", "  \n").replace("<br/>", "  \n").replace("<br />", "  \n")
    s = re.sub(r"</?p[^>]*>", "\n", s)
    s = _RX_TAG.sub("", s)
    s = html.unescape(s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()
