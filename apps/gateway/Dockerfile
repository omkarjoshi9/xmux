FROM python:3.12-slim
WORKDIR /app
COPY apps/gateway/requirements.txt requirements.txt
RUN --mount=type=secret,id=build_ca,required=false \
    if [ -f /run/secrets/build_ca ]; then export PIP_CERT=/run/secrets/build_ca; fi && \
    pip install --no-cache-dir -r requirements.txt && useradd --create-home gateway && mkdir /data && chown gateway /data
COPY --chmod=755 apps apps
COPY --chmod=755 packages packages
ENV GATEWAY_DB=/data/gateway.sqlite PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER gateway
EXPOSE 8080
CMD ["uvicorn", "apps.gateway.src.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
