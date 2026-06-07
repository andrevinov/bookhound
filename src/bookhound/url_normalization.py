from posixpath import normpath
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit


SUPPORTED_SCHEMES = {"http", "https"}
TRACKING_PARAMETERS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "msclkid",
    "utm_campaign",
    "utm_content",
    "utm_medium",
    "utm_source",
    "utm_term",
}


def canonicalize_url(url: str, *, remove_tracking: bool = True) -> str:
    parsed = _parse_supported_url(url)
    scheme = parsed.scheme.lower()
    netloc = _canonical_netloc(parsed.hostname, parsed.port, scheme)
    path = _canonical_path(parsed.path)
    query = _canonical_query(parsed.query, remove_tracking=remove_tracking)
    return urlunsplit((scheme, netloc, path, query, ""))


def is_direct_pdf_url(url: str) -> bool:
    parsed = _parse_supported_url(url)
    path = _canonical_path(parsed.path)
    return path.lower().endswith(".pdf")


def _parse_supported_url(url: str):
    if not url or not url.strip():
        raise ValueError("URL must not be empty.")

    parsed = urlsplit(url.strip())
    if parsed.scheme.lower() not in SUPPORTED_SCHEMES:
        raise ValueError("URL scheme must be http or https.")
    if not parsed.hostname:
        raise ValueError("URL must include a host.")

    return parsed


def _canonical_netloc(hostname: str | None, port: int | None, scheme: str) -> str:
    if hostname is None:
        raise ValueError("URL must include a host.")

    host = hostname.lower()
    if port is None or (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        return host
    return f"{host}:{port}"


def _canonical_path(path: str) -> str:
    if not path:
        return "/"

    trailing_slash = path.endswith("/")
    normalized = normpath(path.replace("//", "/"))
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"

    normalized = quote(normalized, safe="/%:@")
    if trailing_slash and not normalized.lower().endswith(".pdf"):
        return normalized if normalized.endswith("/") else f"{normalized}/"

    if normalized.lower().endswith(".pdf/"):
        return normalized[:-1]

    return normalized


def _canonical_query(query: str, *, remove_tracking: bool) -> str:
    if not query:
        return ""

    parameters = parse_qsl(query, keep_blank_values=True)
    if remove_tracking:
        parameters = [
            (key, value)
            for key, value in parameters
            if key.lower() not in TRACKING_PARAMETERS
        ]

    return urlencode(parameters, doseq=True)
