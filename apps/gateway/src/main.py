import asyncio
import json
import os
from contextlib import asynccontextmanager
from hmac import compare_digest
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from pydantic import ValidationError

from packages.channel_adapters.base import delivery_receipt
from packages.contracts import DeliveryReceipt, DeliveryRequest
from .service import Gateway

MAX_BODY = 256 * 1024


async def read_body(request):
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_BODY:
            raise HTTPException(413, "payload too large")
        data.extend(chunk)
    return bytes(data)


def service_auth(request):
    gateway = request.app.state.gateway
    value = request.headers.get("authorization", "")
    if not compare_digest(value.encode(), ("Bearer " + gateway.secret).encode()):
        raise HTTPException(401, "invalid service credentials")
    return gateway


def create_app(env=os.environ, transport=None, start_worker=True):
    @asynccontextmanager
    async def lifespan(app):
        gateway = app.state.gateway = Gateway(env, transport)
        worker = app.state.worker = asyncio.create_task(gateway.run()) if start_worker else None
        bot = None
        discord_task = None
        try:
            if env.get("DISCORD_GATEWAY_ENABLED", "").lower() == "true":
                from .discord_gateway import create_client
                bot = create_client(gateway, env)
                discord_task = asyncio.create_task(bot.start(gateway.adapters["discord"].token))
            app.state.discord_client = bot
            app.state.discord_task = discord_task
            yield
        finally:
            if bot:
                await bot.close()
            tasks = [task for task in (worker, discord_task) if task is not None]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await gateway.client.aclose()

    api = FastAPI(title="XMUX Channel Gateway", lifespan=lifespan)

    @api.get("/healthz")
    async def health():
        return {"status": "ok"}

    @api.get("/readyz")
    async def ready(request: Request):
        worker = request.app.state.worker
        discord_task = getattr(request.app.state, "discord_task", None)
        if discord_task and (discord_task.done() or not request.app.state.discord_client.is_ready()):
            raise HTTPException(503, "Discord Gateway unavailable")
        if worker and worker.done():
            raise HTTPException(503, "worker unavailable")
        with request.app.state.gateway.store.connection() as db:
            db.execute("SELECT 1 FROM jobs LIMIT 1")
        return {"status": "ready"}

    @api.post("/webhooks/telegram")
    @api.post("/webhooks/discord")
    async def platform_webhook(request: Request):
        return await webhook(request.url.path.rsplit("/", 1)[-1], request)

    async def webhook(platform: str, request: Request):
        gateway = request.app.state.gateway
        adapter = gateway.adapters.get(platform)
        if adapter is None:
            raise HTTPException(404)
        body = await read_body(request)
        if not adapter.verify(request.headers, body):
            raise HTTPException(401, "invalid webhook credentials")
        try:
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise ValueError()
            if platform == "discord":
                if str(payload.get("application_id")) != adapter.application_id:
                    raise HTTPException(401, "incorrect Discord application")
                if payload.get("type") == 1:
                    return {"type": 1}
                events = adapter.parse(payload)
                if not events:
                    return {"type": 4, "data": {"content": "This command is unavailable here.", "flags": 64}}
                token = payload["token"]
                if not isinstance(token, str) or not token.isascii() or not all(
                    char.isalnum() or char in "-_." for char in token
                ) or len(token) > 512 or not token:
                    raise ValueError()
                result = gateway.intake(events[0], token)
                if result == "rate_limited":
                    return {"type": 4, "data": {"content": "Too many requests. Try again shortly.", "flags": 64}}
                return {"type": 5, "data": {"flags": 64}}
            for event in adapter.parse(payload):
                gateway.intake(event)
            return {"ok": True}
        except (ValueError, TypeError, KeyError, OverflowError, AttributeError):
            raise HTTPException(400, "invalid platform payload") from None

    @api.post("/v1/deliveries", response_model=DeliveryReceipt, status_code=202,
              openapi_extra={"requestBody": {"required": True, "content": {"application/json": {
                  "schema": {"$ref": "#/components/schemas/DeliveryRequest"}}}}})
    async def deliver(request: Request):
        gateway = service_auth(request)
        try:
            delivery = DeliveryRequest.model_validate_json(await read_body(request))
        except ValidationError:
            raise HTTPException(422, "invalid delivery") from None
        return gateway.enqueue_delivery(delivery)

    @api.get("/v1/deliveries/{delivery_id}", response_model=DeliveryReceipt)
    async def status(delivery_id: UUID, request: Request):
        row = service_auth(request).store.get(str(delivery_id))
        if row is None:
            raise HTTPException(404, "unknown delivery")
        return delivery_receipt(row)

    original_openapi = api.openapi

    def openapi():
        document = original_openapi()
        schemas = document["components"]["schemas"]
        schema = DeliveryRequest.model_json_schema(ref_template="#/components/schemas/{model}")
        schemas.update(schema.pop("$defs", {}))
        schemas["DeliveryRequest"] = schema
        document["paths"]["/v1/deliveries"]["post"]["security"] = [{"ServiceBearer": []}]
        document["paths"]["/v1/deliveries/{delivery_id}"]["get"]["security"] = [{"ServiceBearer": []}]
        document["components"]["securitySchemes"] = {"ServiceBearer": {"type": "http", "scheme": "bearer"}}
        return document

    api.openapi = openapi
    return api


app = create_app()
