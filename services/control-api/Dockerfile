FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY services/control-api/pyproject.toml ./pyproject.toml
RUN pip install --no-cache-dir .
RUN pip install --no-cache-dir 'pytest>=8,<9'
COPY services/control-api/app ./app
COPY services/control-api/migrations ./migrations
COPY services/control-api/tests ./tests
COPY services/control-api/alembic.ini ./alembic.ini
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
