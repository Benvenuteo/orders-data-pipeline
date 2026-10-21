FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates libltdl7 libkrb5-3 libgssapi-krb5-2 libstdc++6 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.lock pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.lock
# Importing the Python module alone does not load every native ODBC dependency.
# Fail the build immediately if the actual Debian SQL driver cannot be loaded.
RUN python -c "import ctypes; from importlib.resources import files; root=files('mssql_python_odbc') / 'libs/linux/debian_ubuntu/x86_64/lib'; ctypes.CDLL(str(next(root.glob('libmsodbcsql*.so*'))))"
COPY src ./src
RUN pip install --no-cache-dir --no-deps . \
    && useradd --create-home --uid 10001 pipeline \
    && mkdir -p /data /reports && chown pipeline:pipeline /data /reports
USER pipeline
ENTRYPOINT ["orders-pipeline"]
CMD ["run"]
