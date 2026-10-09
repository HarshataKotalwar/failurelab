FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY services/order-service/pyproject.toml ./pyproject.toml
RUN pip install --no-cache-dir .
RUN pip install --no-cache-dir 'pytest>=8,<9'
COPY services/order-service/app ./app
COPY services/order-service/tests ./tests
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser
EXPOSE 8001
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
