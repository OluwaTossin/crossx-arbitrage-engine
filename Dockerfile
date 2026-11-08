FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml requirements.lock ./
COPY src ./src
RUN pip install --no-cache-dir --require-hashes -r requirements.lock && pip install --no-cache-dir --no-deps .
COPY alembic.ini ./
COPY alembic ./alembic
USER 10001:10001
CMD ["sh", "-c", "alembic upgrade head && exec python -m crossx.app"]
