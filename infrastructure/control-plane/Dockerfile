FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /workspace
COPY infrastructure/control-plane/requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir -r /tmp/requirements.lock && useradd --uid 10001 --create-home xmux
COPY apps/orchestrator /workspace/apps/orchestrator
COPY apps/mcp-server /workspace/apps/mcp-server
COPY packages/contracts /workspace/packages/contracts
COPY infrastructure/migrations /workspace/infrastructure/migrations
ENV PYTHONPATH=/workspace/apps/orchestrator/src:/workspace/apps/mcp-server/src
USER xmux
CMD ["uvicorn", "xmux_control.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
