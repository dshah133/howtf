# symsplit dev harness. The tool runs anywhere Python + pyelftools do, but its
# fixtures are real ELF binaries, so test/sweep run in a Linux container.
#
# These targets use `docker run` with an inline dependency install (no
# `docker build`), so they work regardless of the local buildx/builder setup.
# A Dockerfile is also provided if you prefer a pre-baked image.
IMAGE := debian:stable-slim
SETUP := apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
           gcc binutils make python3 python3-pip file >/dev/null 2>&1 && \
         pip install --break-system-packages -q pyelftools==0.31 pytest==8.3.4
RUN := docker run --rm -v "$(CURDIR):/work" -w /work -e PYTHONPATH=/work $(IMAGE) bash -c

.PHONY: test sweep fixtures shell clean

# Build fixtures + run the full pytest suite.
test:
	$(RUN) "$(SETUP) && python3 -m pytest tests/ -q"

# System-binary sweep (acceptance test 3).
sweep:
	$(RUN) "$(SETUP) && python3 sweep.py"

# Build just the ELF fixtures into ./tests/fixtures/*/build.
fixtures:
	$(RUN) "$(SETUP) && bash tests/fixtures/split-state/build.sh && bash tests/fixtures/benign/build.sh"

shell:
	docker run --rm -it -v "$(CURDIR):/work" -w /work -e PYTHONPATH=/work $(IMAGE) bash

clean:
	rm -rf tests/fixtures/*/build .pytest_cache */__pycache__ **/__pycache__
