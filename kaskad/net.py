"""JSON over HTTP with the standard library."""
import json
import urllib.parse
import urllib.request

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def post_json(url, headers, body, timeout):
    """POST body as JSON and return the decoded JSON answer (None for an empty one).

    Requests to this machine skip any configured proxy, so a local model server is always reached.
    """
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
    local = (urllib.parse.urlsplit(url).hostname or "") in _LOOPBACK
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if local else urllib.request.build_opener()
    with opener.open(request, timeout=timeout) as response:  # noqa: S310 - URL comes from configuration
        raw = response.read().decode("utf-8")
    return json.loads(raw) if raw.strip() else None
