.PHONY: install test test-fast coverage schemas benchmark benchmark-scenarios build verify clean

install:
	python -m pip install -e '.[dev,api]'

test:
	PYTHONPATH=src pytest -W error::ResourceWarning

test-fast:
	PYTHONPATH=src pytest -m 'not performance' -W error::ResourceWarning

coverage:
	PYTHONPATH=src pytest --cov=governed_harness --cov-branch \
		--cov-report=term-missing --cov-report=xml:reports/coverage.xml \
		--cov-report=json:reports/coverage.json --junitxml=reports/junit.xml

schemas:
	PYTHONPATH=src python scripts/generate_schemas.py

benchmark:
	PYTHONPATH=src python -m governed_harness benchmark run --iterations 500 \
		--output reports/benchmark-micro.json

benchmark-scenarios:
	PYTHONPATH=src python -m governed_harness benchmark scenarios --iterations 3 \
		--output reports/benchmark-scenarios.json

build:
	python -m build

verify: schemas test coverage benchmark benchmark-scenarios build

clean:
	rm -rf build dist .pytest_cache .coverage htmlcov
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete
