.PHONY: up down test lint format migrate logs

up:
	docker compose up --build

down:
	docker compose down

test:
	set -e; for service in control-api inventory-service payment-service order-service; do (cd services/$$service && ../../.venv/bin/python -m pytest); done
	.venv/bin/python -m pytest -m integration tests/integration

lint:
	.venv/bin/python -m ruff check services

format:
	.venv/bin/python -m ruff format services

migrate:
	docker compose exec -T control-api alembic upgrade head

logs:
	docker compose logs -f
