"""Outbound announce-source URL policy (pure logic, sandbox clean).

The daemon can pull the announce queue from a PC-side exporter over HTTP each
poll (design.md §M3.3.1 / §M3.1-s.5). The *network call* lives in the ability
layer (`openhome_ability/background.py`); this module holds only the pure,
unit-testable policy that decides whether a configured source URL may be fetched
at all.

ONE-WAY + sandbox guard (design.md §3.1 / §M3.1-s.2): only `http://` / `https://`
URLs are honored. `urllib.request.urlopen` will also resolve `file://`, `ftp://`
and `data:` URLs — a mistyped or hostile `ANNOUNCE_SOURCE_URL` could otherwise
turn the queue pull into an arbitrary **local-file read**, silently reversing the
"no raw file access" sandbox redesign and the one-way guarantee. So the ability
calls `is_http_url()` before every fetch and skips anything that is not http(s).

Pure logic: no I/O, no data-encoding, no platform access — stays sandbox-clean
and is shared with the sister project unchanged.
"""

# The only URL schemes the daemon is allowed to GET (LAN exporter is plain http).
ALLOWED_SCHEMES = ("http://", "https://")


def is_http_url(url) -> bool:
    """True only for a non-empty `http://` / `https://` URL string.

    Scheme match is case-insensitive ("HTTP://" is fine). Anything else — a
    non-string, the empty string, or a `file://` / `ftp://` / `data:` URL — is
    rejected so the queue pull can never become a local-file read.
    """
    if not isinstance(url, str) or not url:
        return False
    return url.lower().startswith(ALLOWED_SCHEMES)
