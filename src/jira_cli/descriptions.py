"""Safe, terminal-only rendering; original REST descriptions stay intact."""

import re
from html.parser import HTMLParser

# Code is opaque to both HTML and wiki link conversion.
CODE = re.compile(
    r"(```[^\n]*\n[\s\S]*?(?:```|$)|`[^`\n]*`"
    r"|\{code(?::[^}]*)?\}[\s\S]*?(?:\{code\}|$)"
    r"|\{noformat\}[\s\S]*?(?:\{noformat\}|$)|\{\{[^\n]*?\}\})",
    re.IGNORECASE,
)
WIKI_LINK = re.compile(r"\[([^\]\n|]+)\|(https?://[^\]\n]+)\]|\[(https?://[^\]\n]+)\]")
BARE_AMPERSAND = re.compile(r"&(?!\#[0-9]+;|\#[xX][0-9a-fA-F]+;|[A-Za-z][A-Za-z0-9]+;)")
BLOCK_TAGS = {"p", "div", "ul", "ol", "blockquote", "h1", "h2", "h3", "tr"}


class DescriptionParser(HTMLParser):
    """Extract readable content without running HTML, scripts, or terminal markup."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden: list[str] = []
        self.anchors: list[tuple[str, int]] = []
        self.code: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.code:
            if tag in {"pre", "code"}:
                self.code.append(tag)
            else:
                self.parts.append(self.get_starttag_text() or "")
            return
        if tag in {"pre", "code"} and not self.hidden:
            self.code.append(tag)
            if tag == "pre":
                self.parts.append("\n")
            return
        if tag in {"script", "style"}:
            self.hidden.append(tag)
        if self.hidden:
            return
        if tag in BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "br":
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("\n* ")
        if tag == "a":
            self.anchors.append((dict(attrs).get("href") or "", len(self.parts)))
        if tag == "img":
            self.parts.append(dict(attrs).get("alt") or "")

    def handle_endtag(self, tag: str) -> None:
        if self.code:
            if tag == self.code[-1]:
                self.code.pop()
                if tag == "pre":
                    self.parts.append("\n")
            else:
                self.parts.append("</" + tag + ">")
            return
        if self.hidden:
            if tag == self.hidden[-1]:
                self.hidden.pop()
            return
        if tag == "a" and self.anchors:
            self.finish_anchor()
        if tag in BLOCK_TAGS or tag == "li":
            self.parts.append("\n")
        if tag in {"td", "th"}:
            self.parts.append("  ")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data if self.code else wiki_links(data))

    def close(self) -> None:
        super().close()
        while self.anchors:
            self.finish_anchor()

    def finish_anchor(self) -> None:
        destination, start = self.anchors.pop()
        label = "".join(self.parts[start:]).strip()
        if destination.startswith(("https://", "http://")) and label != destination:
            self.parts.append(": " + destination)


def wiki_links(value: str) -> str:
    return WIKI_LINK.sub(
        lambda match: (match[1] + ": " + match[2]) if match[2] else match[3], value
    )


def description(value: object) -> object:
    if not isinstance(value, str) or not value:
        return value
    result = []
    for index, part in enumerate(CODE.split(value)):
        if index % 2:
            result.append(part)
            continue
        parser = DescriptionParser()
        # HTML permits semicolon-free named entities, but plain URL query keys
        # such as &copy=1 must remain literal. Decode explicit entities once.
        parser.feed(BARE_AMPERSAND.sub("&amp;", part))
        parser.close()
        rendered = "".join(parser.parts)
        # HTMLParser already decoded once. Never repeatedly unescape its output.
        result.append(rendered)
    return "".join(result).strip("\n").expandtabs(4)
