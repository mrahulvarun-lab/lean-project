# Convenience targets. Plain `pip`/`uvicorn`/`pytest` commands work too — see README.
.PHONY: help setup seed run test scenarios clean

help:
	@echo "make setup      install dependencies"
	@echo "make seed       build circles.db from data/*.json"
	@echo "make run        start the API on http://localhost:8000"
	@echo "make test       run the test suite"
	@echo "make scenarios  print the four planted scenarios"
	@echo "make clean      remove the generated database"

setup:
	pip install -r requirements.txt

seed:
	python -m app.seed

run: seed
	uvicorn app.main:app --reload

test:
	pytest -q

scenarios:
	python -m app.cli --scenarios

clean:
	rm -f circles.db
