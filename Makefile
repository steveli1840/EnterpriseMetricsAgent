.PHONY: test test-backend test-frontend build up down

test: test-backend test-frontend

test-backend:
	PYTHONPATH=backend python3 -m pytest backend/tests

test-frontend:
	cd frontend && npm test

build:
	cd frontend && npm run build

up:
	docker compose up --build

down:
	docker compose down

