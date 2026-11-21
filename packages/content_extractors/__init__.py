import asyncio
import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

import aiohttp
from aiohttp.abc import AbstractResolver
from pydantic import HttpUrl

from packages.contracts import Source


def normalize_url(value: str) -> str:
    if any(ord(c) <= 32 or c == "\\" for c in value):
        raise ValueError("invalid URL")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None:
        raise ValueError("invalid URL")
    if parsed.port is not None and parsed.port != (443 if parsed.scheme == "https" else 80):
        raise ValueError("unsupported port")
    return urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path or "/", parsed.query, ""))


def extract_urls(text: str, extra=()) -> list[str]:
    urls = {}
    for value in [*extra, *re.findall(r'https?://[^\s<>"\']+', text, flags=re.I)]:
        try:
            urls[str(HttpUrl(normalize_url(value.rstrip(".,;!?"))))] = None
        except ValueError:
            continue
    return list(urls)[:5]


def check_address(value: str) -> None:
    address = ipaddress.ip_address(value)
    if (not address.is_global or address.is_multicast or getattr(address, "ipv4_mapped", None)
            or (address.version == 6 and address not in ipaddress.ip_network("2000::/3"))
            or getattr(address, "sixtofour", None) or getattr(address, "teredo", None)):
        raise ValueError("non-public destination")


class PublicResolver(AbstractResolver):
    async def resolve(self, host, port=0, family=socket.AF_UNSPEC):
        records = await asyncio.get_running_loop().getaddrinfo(host, port, family=family, type=socket.SOCK_STREAM)
        addresses = []
        for af, _, proto, _, endpoint in records:
            check_address(endpoint[0])
            addresses.append(dict(hostname=host, host=endpoint[0], port=port, family=af, proto=proto, flags=0))
        return addresses

    async def close(self):
        pass


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hidden = None
        self.in_title = False
        self.title = []
        self.text = []
        self.description = ""
        self.canonical = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"script", "style", "noscript"}:
            self.hidden = tag
        elif tag == "title":
            self.in_title = True
        elif tag == "meta" and (attrs.get("name") or "").lower() == "description":
            self.description = (attrs.get("content") or "")[:500]
        elif tag == "link" and (attrs.get("rel") or "").lower() == "canonical":
            self.canonical = attrs.get("href") or ""

    def handle_endtag(self, tag):
        if self.hidden == tag:
            self.hidden = None
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if not self.hidden:
            (self.title if self.in_title else self.text).append(data)


async def fetch_public_page(url: str) -> Source:
    try:
        async with asyncio.timeout(8), aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(resolver=PublicResolver(), use_dns_cache=False),
            trust_env=False, cookie_jar=aiohttp.DummyCookieJar(),
            auto_decompress=False, headers={"Accept-Encoding": "identity"},
        ) as session:
            current = url
            for _ in range(4):
                current = normalize_url(current)
                hostname = urlsplit(current).hostname
                try:
                    ipaddress.ip_address(hostname)
                except ValueError:
                    pass
                else:
                    check_address(hostname)
                async with session.get(current, allow_redirects=False) as response:
                    if response.status in {301, 302, 303, 307, 308}:
                        current = urljoin(current, response.headers["Location"])
                        continue
                    if response.status != 200 or response.headers.get("Content-Encoding", "identity") != "identity":
                        break
                    if response.content_type not in {"text/html", "application/xhtml+xml"}:
                        break
                    body = bytearray()
                    async for chunk in response.content.iter_chunked(16384):
                        body.extend(chunk)
                        if len(body) > 512 * 1024:
                            raise ValueError("page too large")
                    parser = PageParser()
                    parser.feed(body.decode(response.charset or "utf-8", errors="replace"))
                    try:
                        canonical = str(HttpUrl(normalize_url(urljoin(current, parser.canonical))))
                    except ValueError:
                        canonical = current
                    return Source(url=url, status="available", canonical_url=canonical,
                                  title=" ".join(parser.title).strip()[:300],
                                  description=parser.description, excerpt=" ".join(" ".join(parser.text).split())[:800])
    except (ValueError, LookupError, OSError, aiohttp.ClientError, TimeoutError):
        pass
    return Source(url=url, status="content_unavailable")
