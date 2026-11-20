"""OpenBot signed webhook routine transport.

OpenBot acceptance indicates event delivery, not that a routine ran.
Configure the routine URL and secret separately for each destination.
"""
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class OpenBotDelivery:
    delivery_id: str
    accepted: bool
    duplicate: bool


class OpenBotWebhook:
    def __init__(self, url: str, signing_secret: Callable[[], str], opener=None, timeout=10):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.fragment:
            raise ValueError("invalid_openbot_url")
        if timeout <= 0:
            raise ValueError("invalid_timeout")
        self._url, self._secret, self._timeout = url, signing_secret, timeout
        self._opener = opener or build_opener(NoRedirect())

    def submit(self, *, tenant_id: str, task_id: str, idempotency_key: str, objective: str,
               repository: str, branch: str, allowed_tools: tuple[str, ...], authorize: Callable[[], bool]):
        if not authorize():
            raise PermissionError("coding_scope_not_approved")
        if not all((tenant_id, task_id, idempotency_key, objective, repository, branch)):
            raise ValueError("missing_task_fields")
        identity = json.dumps([tenant_id, task_id, idempotency_key], separators=(",", ":"))
        delivery_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        body = json.dumps({"type": "xmux.task", "data": {"task_id": task_id, "objective": objective,
                          "repository": repository, "branch": branch, "allowed_tools": list(allowed_tools)}},
                          ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(body) > 65536:
            raise ValueError("openbot_body_too_large")
        timestamp = str(int(time.time()))
        signed = timestamp.encode() + b"." + delivery_id.encode() + b"." + body
        signature = hmac.new(self._secret().encode(), signed, hashlib.sha256).hexdigest()
        request = Request(self._url, data=body, method="POST", headers={
            "Content-Type": "application/json",
            "X-OpenBot-Timestamp": timestamp,
            "X-OpenBot-Delivery-Id": delivery_id,
            "X-OpenBot-Signature": "sha256=" + signature,
        })
        with self._opener.open(request, timeout=self._timeout) as response:
            if response.status not in (200, 202):
                raise RuntimeError("openbot_delivery_rejected")
            return OpenBotDelivery(delivery_id, True, response.status == 200)
