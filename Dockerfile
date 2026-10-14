FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY gateway ./gateway
RUN pip install --no-cache-dir . \
 && useradd --system --uid 10001 gateway \
 && mkdir /data && chown gateway /data

USER gateway
ENV DB_PATH=/data/gateway.sqlite3
EXPOSE 8000
# --no-access-log: в строке запроса /sso лежит токен площадки, в журнал он попасть не должен.
CMD ["uvicorn", "--factory", "gateway.app:create_app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
