from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, field_validator

from xmux_control.auth import authenticate
from xmux_control.phase6_router import Decision
from xmux_control.routines import Routines


class ScheduleBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    timezone: str = Field(min_length=1, max_length=100)
    recurrence: str = Field(min_length=1, max_length=1000, pattern=r'\S')

    @field_validator('timezone')
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError('invalid_timezone') from exc
        return value


class RoutineBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    idempotency_key: str = Field(min_length=1, max_length=255)
    agent_id: str = Field(min_length=1, max_length=512)
    request: ScheduleBody


AUTH = Depends(authenticate)


def install(app, ledger, remote):
    router = APIRouter()
    service = Routines(ledger, remote)

    @router.get('/v1/routines')
    def list_routines(actor=AUTH):
        return {'items': service.list(actor)}

    @router.post('/v1/routines')
    def create(body: RoutineBody, actor=AUTH):
        return service.create(actor, body.idempotency_key, body.agent_id, body.request.model_dump())

    @router.get('/v1/routines/{identity}')
    def status(identity: UUID, actor=AUTH):
        return service.get(actor, identity)

    @router.post('/v1/routines/{identity}/reconcile')
    def reconcile(identity: UUID, actor=AUTH):
        return service.reconcile(actor, identity)

    @router.post('/v1/routines/{identity}/decision')
    def decide(identity: UUID, body: Decision, actor=AUTH):
        return service.decide(actor, identity, body.action_fingerprint, body.decision)

    app.include_router(router)
