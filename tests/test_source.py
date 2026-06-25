"""Source-URL policy: http(s) only (design.md §M3.3.1 / §3.1).

`is_http_url` is the pure guard the daemon runs before fetching the announce
queue. It must accept LAN http(s) exporter URLs and reject anything that would
turn `urllib.request.urlopen` into a local-file / non-network read (file://,
ftp://, data:) — that would silently reverse the "no raw file access" sandbox
redesign and the one-way guarantee.
"""

from approval_voice.source import is_http_url


def test_accepts_http_and_https():
    assert is_http_url("http://192.168.1.20:8000/announce_queue.json")
    assert is_http_url("https://example.local/announce_queue.json")


def test_scheme_match_is_case_insensitive():
    assert is_http_url("HTTP://host:8000/q.json")
    assert is_http_url("HtTpS://host/q.json")


def test_rejects_non_network_schemes():
    # These are exactly the urlopen-resolvable schemes that must never be fetched.
    assert not is_http_url("file:///etc/passwd")
    assert not is_http_url("file://C:/secret/state.json")
    assert not is_http_url("ftp://host/q.json")
    assert not is_http_url("data:application/json,[]")


def test_rejects_empty_and_non_string():
    assert not is_http_url("")
    assert not is_http_url(None)
    assert not is_http_url(123)
    assert not is_http_url(["http://x"])


def test_rejects_lookalike_scheme():
    # A scheme that merely starts with "http" but is not http(s) is rejected.
    assert not is_http_url("httpx://host/q.json")
    assert not is_http_url("javascript:alert(1)")
