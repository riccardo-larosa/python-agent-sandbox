.PHONY: docker test

docker: 
	docker build -t python-chart-sandbox:latest .

test:
	uv run pytest -v

