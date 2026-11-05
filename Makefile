.PHONY: demo test api

demo:
	python -m investment_engine.cli --source fixture --out output/latest-preview.json

test:
	python -m pytest -q

api:
	uvicorn investment_engine.api:app --host 127.0.0.1 --port 8018
