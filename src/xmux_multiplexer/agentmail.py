"""AgentMail API client. Store API keys outside source control."""

import json
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class AgentMailClient:
    def __init__(self, api_key: str, opener=None, timeout: float = 10):
        if not api_key or timeout <= 0:
            raise ValueError("agentmail_configuration_invalid")
        self._api_key = api_key
        self._opener = opener or build_opener(NoRedirect())
        self._timeout = timeout

    def _request(self, method, path, payload=None, idempotency_key=None):
        headers = {"Authorization": "Bearer " + self._api_key, "Content-Type": "application/json"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request("https://api.agentmail.to" + path, data=body, headers=headers, method=method)
        with self._opener.open(request, timeout=self._timeout) as response:
            raw = response.read(1048577)
        if len(raw) > 1048576:
            raise ValueError("agentmail_response_too_large")
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError("invalid_agentmail_response")
        return result

    def create_inbox(self, client_id: str):
        if not client_id:
            raise ValueError("client_id_required")
        return self._request("POST", "/v0/inboxes", {"display_name": "XMUX", "client_id": client_id})

    def send(self, inbox_id: str, recipient: str, subject: str, body: str, idempotency_key: str):
        if not all((inbox_id, recipient, idempotency_key)):
            raise ValueError("message_fields_required")
        path = f"/v0/inboxes/{quote(inbox_id, safe='')}/messages/send"
        return self._request("POST", path, {"to": recipient, "subject": subject, "text": body}, idempotency_key)

    def get_thread(self, inbox_id: str, thread_id: str):
        path = f"/v0/inboxes/{quote(inbox_id, safe='')}/threads/{quote(thread_id, safe='')}"
        return self._request("GET", path)
