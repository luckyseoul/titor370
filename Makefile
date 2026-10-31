PY ?= python3

.PHONY: all test examples compare clean

all: test compare

test:
	$(PY) -m pytest -q

compare:
	@for f in examples/*.s370; do \
	  printf '%-24s ' "$$(basename $$f)"; \
	  $(PY) -m titor370.cli compare "$$f" | head -1; \
	done

examples: compare

clean:
	rm -rf __pycache__ */__pycache__ .pytest_cache *.egg-info
