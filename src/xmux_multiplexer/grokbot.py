"""Grok Bot webhook dispatch; this API accepts runs but exposes no run tracking."""

from dataclasses import dataclass
import json
from typing import Callable, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class SecretResolver(Protocol):
    def __call__(self, reference: str) -> str: ...


class HttpTransport(Protocol):
    def post(self, url: str, headers: Mapping[str, str], body: bytes | None,
             timeout: float) -> int: ...


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class UrllibTransport:
    def post(self, url: str, headers: Mapping[str, str], body: bytes | None,
             timeout: float) -> int:
        request = Request(url, data=body, headers=dict(headers), method="POST")
        try:
            with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
                return response.status
        except HTTPError as error:
            return error.code
        except (TimeoutError, URLError) as error:
            raise WebhookOutcomeUnknown("request outcome is unknown; do not retry blindly") from error


class WebhookOutcomeUnknown(RuntimeError):
    """A transport failure may have happened after Grok Bot accepted the POST."""


@dataclass(frozen=True)
class WebhookAcceptance:
    accepted: bool


class GrokBotWebhook:
    id = "grokbot"
    ALLOWED_SCOPES = frozenset({"routine:trigger"})

    def __init__(self, url: str, secret_reference: str, secret_resolver: SecretResolver,
                 scopes: frozenset[str], transport: HttpTransport | None = None,
                 timeout: float = 10.0) -> None:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.fragment:
            raise ValueError("webhook_url_must_use_https")
        if not scopes or not scopes.issubset(self.ALLOWED_SCOPES):
            raise ValueError("unsupported_scope")
        if timeout <= 0:
            raise ValueError("timeout_must_be_positive")
        self.url, self.secret_reference, self._resolve = url, secret_reference, secret_resolver
        self.scopes, self._transport, self.timeout = scopes, transport or UrllibTransport(), timeout

    def capabilities(self) -> frozenset[str]:
        return frozenset({"routine:trigger"})

    def submit(self, payload: Mapping[str, object] | None = None) -> WebhookAcceptance:
        secret = self._resolve(self.secret_reference)
        if not secret:
            raise ValueError("empty_webhook_secret")
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Authorization": f"Bearer {secret}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        try:
            status = self._transport.post(self.url, headers, body, self.timeout)
        except (TimeoutError, WebhookOutcomeUnknown) as error:
            raise WebhookOutcomeUnknown("request outcome is unknown; do not retry blindly") from error
        if status == 200:
            return WebhookAcceptance(accepted=True)
        return WebhookAcceptance(accepted=False)
