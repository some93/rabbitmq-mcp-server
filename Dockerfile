FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

COPY src/ ./src/
COPY config.yaml ./

RUN useradd -m rmquser && chown -R rmquser:rmquser /app
USER rmquser

ENTRYPOINT ["python", "-m", "rabbitmq_mcp_server.server"]
