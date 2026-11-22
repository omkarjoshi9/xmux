from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, field_validator

from xmux_control.approvals import Approvals
from xmux_control.auth import authenticate
from xmux_control.workflows import Workflows, validate


class Decision(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    action_fingerprint: str = Field(pattern=r'^[0-9a-f]{64}$')
    decision: Literal['approve', 'deny']


class WorkflowBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    idempotency_key: str = Field(min_length=1, max_length=200)
    steps: list[dict] = Field(min_length=2, max_length=3)

    @field_validator('steps')
    @classmethod
    def valid_steps(cls, steps):
        validate(steps)
        return steps


AUTH = Depends(authenticate)


def install(app, ledger):
    router = APIRouter()

    @router.post('/v1/approvals/{approval_id}')
    def decide(approval_id: UUID, body: Decision, actor=AUTH):
        return {'state': Approvals(ledger).decide(actor, approval_id, body.action_fingerprint, body.decision)}

    @router.post('/v1/workflows')
    def create(body: WorkflowBody, actor=AUTH):
        return {'id': Workflows(ledger).create(actor, body.steps, body.idempotency_key)}

    @router.get('/v1/workflows/{workflow_id}')
    def status(workflow_id: UUID, actor=AUTH):
        return {'state': Workflows(ledger).advance(actor, workflow_id)}

    app.include_router(router)
