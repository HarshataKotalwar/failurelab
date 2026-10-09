FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY services/payment-service/pyproject.toml ./pyproject.toml
RUN pip install --no-cache-dir .
RUN pip install --no-cache-dir 'pytest>=8,<9' 'httpx>=0.27,<1'
COPY services/payment-service/app ./app
COPY services/payment-service/tests ./tests
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser
EXPOSE 8003
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8003"]
