FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates libgssapi-krb5-2 libstdc++6 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.lock pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.lock
COPY src ./src
RUN pip install --no-cache-dir --no-deps . \
    && useradd --create-home --uid 10001 pipeline \
    && mkdir -p /data /reports && chown pipeline:pipeline /data /reports
USER pipeline
ENTRYPOINT ["orders-pipeline"]
CMD ["run"]
