"""HTTP transport for Paperclip's documented issues API."""
import json
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class PaperclipHttpClient:
    def __init__(self, endpoint, credential, *, timeout=10, opener=None):
        parsed = urlsplit(endpoint)
        if (parsed.scheme != "https" or not parsed.netloc or parsed.username
                or parsed.password or parsed.query or parsed.fragment or timeout <= 0):
            raise ValueError("invalid_paperclip_endpoint")
        self._endpoint = endpoint.rstrip("/")
        self._credential = credential
        self._timeout = timeout
        self._opener = opener or build_opener(NoRedirect())

    def _request(self, method, path, body=None):
        if not path.startswith("/api/") or path.startswith("//") or "?" in path or "#" in path:
            raise ValueError("invalid_paperclip_path")
        token = self._credential()
        if not isinstance(token, str) or not token:
            raise ValueError("paperclip_credential_missing")
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        request = Request(self._endpoint + path, data=payload, method=method,
                          headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        with self._opener.open(request, timeout=self._timeout) as response:
            raw = response.read(1048577)
        if len(raw) > 1048576:
            raise ValueError("paperclip_response_too_large")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("invalid_paperclip_response")
        return value

    def post(self, path, body):
        return self._request("POST", path, body)

    def get(self, path):
        return self._request("GET", path)

    def issue_reference(self, issue_id):
        if not isinstance(issue_id, str) or not issue_id:
            raise ValueError("issue_id_required")
        return self._endpoint + "/api/issues/" + quote(issue_id, safe="")
