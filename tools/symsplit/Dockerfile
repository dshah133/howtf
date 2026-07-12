# Reproducible ELF test/sweep environment for symsplit.
# The tool is pure Python + pyelftools, but its fixtures are real ELF binaries,
# so building/running them needs a Linux/ELF toolchain.
FROM debian:stable-slim

RUN apt-get update -qq \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
      gcc binutils make python3 python3-pip file \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --break-system-packages --no-cache-dir \
      pyelftools==0.31 pytest==8.3.4

WORKDIR /work
COPY . /work
ENV PYTHONPATH=/work
CMD ["python3", "-m", "pytest", "tests/", "-q"]
