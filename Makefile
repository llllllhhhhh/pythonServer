.PHONY: dev worker install migrate migration-check up down logs test

install:
	python -m pip install -r requirements.txt

dev:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

worker:
	rq worker --with-scheduler --url $${REDIS_URL:-redis://127.0.0.1:6379/0} xuetuxing-orders

migrate:
	python -m app.db_migrate

migration-check:
	alembic check

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f api worker

test:
	python -m unittest discover -s tests -v
