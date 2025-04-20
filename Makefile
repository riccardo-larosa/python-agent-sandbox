.PHONY: docker test server

docker: 
	docker build -t python-chart-sandbox:latest .

test:
	uv run pytest -v

server:
	uvicorn src.main:app --reload --host 0.0.0.0 --port 8002

test-agent:
	uv run python tests/test_agent.py