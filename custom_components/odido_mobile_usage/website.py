"""Parse only the visible usage sections observed on Mijn Odido.

No analytics, account identifiers, individual calls, scripts or hidden fields
are included in the normalized usage model.
"""

import re
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser


class UsageParseError(ValueError):
    """The page does not contain the expected usage sections."""


@dataclass
class Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    parts: list = field(default_factory=list)

    def text(self):
        if self.tag in {"script", "style", "svg", "template"}:
            return ""
        return " ".join(p.text() if isinstance(p, Node) else p for p in self.parts)

    def find(self, tag=None, node_id=None):
        result = []
        if (tag is None or self.tag == tag) and (
            node_id is None or self.attrs.get("id") == node_id
        ):
            result.append(self)
        for part in self.parts:
            if isinstance(part, Node):
                result.extend(part.find(tag, node_id))
        return result


class TreeParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("root")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs))
        self.stack[-1].parts.append(node)
        if tag not in {
            "input",
            "img",
            "br",
            "hr",
            "meta",
            "link",
            "source",
            "wbr",
            "area",
            "base",
            "embed",
            "param",
            "track",
            "col",
        }:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if self.stack[-1].tag == tag:
            self.stack.pop()

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].parts.append(data)


def text(node):
    return re.sub(r"\s+", " ", node.text()).strip()


def number(value):
    """Dutch presentation: 10.000 = ten thousand, 10,5 = ten and a half."""
    try:
        result = Decimal(value.replace(".", "").replace(",", ".").replace(" ", ""))
    except InvalidOperation:
        raise UsageParseError("Invalid numeric value") from None
    if not result.is_finite() or result < 0:
        raise UsageParseError("Invalid numeric value")
    return result


def gigabytes(value, unit):
    result = number(value)
    return float(result / 1000 if unit.upper() == "MB" else result)


@dataclass(frozen=True)
class UsageData:
    used_gb: float
    remaining_gb: float
    limit_gb: float
    percentage: float
    voice_used_minutes: float | None = None
    voice_allowance: str | None = None
    extra_costs: float | None = None
    reported_data_used_gb: float | None = None
    sms_used: int | None = None

    def as_dict(self):
        return asdict(self)


def parse_usage(allowance_html, details_html):
    """Read allowance and monthly details separately; they can update differently."""
    allowance = TreeParser()
    allowance.feed(allowance_html)
    internet = allowance.root.find(node_id="internet")
    if len(internet) != 1:
        raise UsageParseError("Internet section missing")
    fractions = []
    for heading in internet[0].find("h4"):
        match = re.fullmatch(
            r"([\d.,]+)\s*(MB|GB)\s+over van\s+([\d.,]+)\s*(MB|GB)",
            text(heading),
            re.IGNORECASE,
        )
        if match:
            fractions.append(match)
    # Avoid silently choosing one of several overlapping domestic/roaming bundles.
    if len(fractions) != 1:
        raise UsageParseError("Expected one domestic data allowance")
    match = fractions[0]
    remaining = gigabytes(match[1], match[2])
    limit = gigabytes(match[3], match[4])
    if remaining > limit or limit <= 0:
        raise UsageParseError("Inconsistent allowance")
    used = round(limit - remaining, 6)
    voice = allowance.root.find(node_id="calltext")
    voice_allowance = None
    if voice:
        headings = [text(h) for h in voice[0].find("h4")]
        voice_allowance = "Onbeperkt" if "Onbeperkt" in headings else None

    details = TreeParser()
    details.feed(details_html)
    main = details.root.find("main")
    if len(main) != 1:
        raise UsageParseError("Usage details missing")
    totals = [
        row
        for row in main[0].find("tr")
        if row.find("td") and text(row.find("td")[0]) == "Totaal"
    ]
    if len(totals) != 1:
        raise UsageParseError("Monthly total row missing")
    # On desktop the labels come from table headers; on mobile the website
    # uses data-table-header via CSS. They are not part of the cell text.
    row_text = " ".join(
        cell.attrs.get("data-table-header", "") + " " + text(cell)
        for cell in totals[0].find("td")
    )
    data = re.search(r"Data\s+([\d.,]+)\s*(MB|GB)\b", row_text)
    minutes = re.search(r"Belminuten\s+(\d+):(\d{2})\s*min\b", row_text)
    sms = re.search(r"/\s*(\d+)\s*sms\b", row_text)
    costs = re.search(r"Extra Kosten\s*€\s*([\d.,]+)", row_text, re.IGNORECASE)
    if not data or not minutes or not costs or int(minutes[2]) >= 60:
        raise UsageParseError("Monthly usage fields missing or invalid")
    return UsageData(
        used_gb=used,
        remaining_gb=remaining,
        limit_gb=limit,
        percentage=round(used / limit * 100, 2),
        voice_used_minutes=round(int(minutes[1]) + int(minutes[2]) / 60, 6),
        voice_allowance=voice_allowance,
        reported_data_used_gb=gigabytes(data[1], data[2]),
        extra_costs=float(number(costs[1])),
        sms_used=int(sms[1]) if sms else None,
    )
