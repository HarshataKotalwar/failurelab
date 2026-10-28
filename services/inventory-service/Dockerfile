FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY services/inventory-service/pyproject.toml ./pyproject.toml
RUN pip install --no-cache-dir .
RUN pip install --no-cache-dir 'pytest>=8,<9' 'httpx>=0.27,<1'
COPY services/shared/failurelab_observability.py ./failurelab_observability.py
COPY services/shared/failurelab_failure.py ./failurelab_failure.py
COPY services/inventory-service/app ./app
COPY services/inventory-service/tests ./tests
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser
EXPOSE 8002
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8002"]
