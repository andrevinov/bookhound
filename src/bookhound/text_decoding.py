from __future__ import annotations

from collections.abc import Mapping
from email.message import Message


FALLBACK_ENCODINGS = ("windows-1252", "latin-1")


def decode_http_text(content: bytes, headers: Mapping[str, str]) -> str:
    encodings = _candidate_encodings(headers)
    for encoding in encodings:
        try:
            return content.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return content.decode("utf-8", errors="replace")


def _candidate_encodings(headers: Mapping[str, str]) -> list[str]:
    encodings: list[str] = []
    declared_charset = _declared_charset(headers)
    if declared_charset is not None:
        encodings.append(declared_charset)
    encodings.append("utf-8")
    encodings.extend(FALLBACK_ENCODINGS)
    return list(dict.fromkeys(encodings))


def _declared_charset(headers: Mapping[str, str]) -> str | None:
    content_type = _header_value(headers, "content-type")
    if content_type is None:
        return None

    message = Message()
    message["content-type"] = content_type
    charset = message.get_content_charset()
    if charset is None:
        return None
    return charset.strip() or None


def _header_value(headers: Mapping[str, str], name: str) -> str | None:
    for header_name, value in headers.items():
        if header_name.lower() == name:
            return value
    return None
