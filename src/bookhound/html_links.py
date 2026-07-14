from dataclasses import dataclass
from html.parser import HTMLParser


@dataclass(frozen=True)
class HtmlLink:
    href: str
    text: str


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[HtmlLink] = []
        self._current_href: str | None = None
        self._current_text: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag.lower() != "a":
            return
        attributes = {
            name.lower(): value.strip()
            for name, value in attrs
            if value is not None
        }
        href = attributes.get("href")
        if href:
            self._current_href = href
            self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._current_href is not None:
            self._current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._current_href is None:
            return
        self.links.append(
            HtmlLink(
                href=self._current_href,
                text=" ".join("".join(self._current_text).split()),
            )
        )
        self._current_href = None
        self._current_text = []


def parse_links(html: str) -> list[HtmlLink]:
    parser = _LinkParser()
    parser.feed(html)
    return parser.links
