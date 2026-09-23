.PHONY: install run test bench load docker zip clean

install:
	python3 -m venv .venv
	.venv/bin/pip install -r requirements-dev.txt

run:
	PYTHONPATH=src .venv/bin/python -m uvicorn pdguard.main:app --host 0.0.0.0 --port 8000 --reload

test:
	.venv/bin/python -m pytest

bench:
	.venv/bin/python bench/bench_pipeline.py --iterations 3000

load:
	.venv/bin/python bench/loadtest.py --url http://localhost:8000 --duration 30 --concurrency 64

docker:
	docker compose up --build

zip:
	rm -f ../alfa-pd-guard-submission.zip
	zip -rq ../alfa-pd-guard-submission.zip . -x ".venv/*" "*.pyc" "*/__pycache__/*" "__pycache__/*" ".pytest_cache/*" "*.egg-info/*" ".git/*" ".DS_Store" "*.gif" ".env" ".env.*"
	@unzip -l ../alfa-pd-guard-submission.zip | grep -qE '(^|/)\.env' && { echo "ОШИБКА: в архив попал .env"; exit 1; } || echo "архив собран, секретов внутри нет"

clean:
	rm -rf .pytest_cache **/__pycache__
