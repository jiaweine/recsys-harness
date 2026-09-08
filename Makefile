.PHONY: run test check demo demo-json tour release-check release-build clean

run:
	uvicorn lingjing_harness.api:app --host 0.0.0.0 --port 8765 --reload

test:
	pytest -q

check:
	python -m compileall -q lingjing_harness tests scripts
	node --check frontend/app.js

demo: tour

tour:
	python scripts/mission_tour.py

demo-json:
	python -m lingjing_harness.cli '最近搜索“露营灯”的结果不准，帮我优化，但先不要上线'

release-check:
	python scripts/check_release_contract.py

release-build: release-check
	rm -rf build dist *.egg-info
	python -m build --sdist --wheel --outdir dist
	python scripts/verify_release_artifacts.py dist

clean:
	rm -rf .pytest_cache data build dist *.egg-info __pycache__ */__pycache__ */*/__pycache__
