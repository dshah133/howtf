"""Shared fixtures: build the ELF fixture matrices once per test session.

symsplit's fixtures are real ELF binaries, so the tests require a Linux/ELF
toolchain (gcc + binutils). On a non-ELF host (e.g. macOS) the tests skip with
a clear message -- run them inside the project's Linux container instead
(see tools/symsplit/README.md).
"""
import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(__file__)
SPLIT_STATE = os.path.join(HERE, "fixtures", "split-state")
BENIGN = os.path.join(HERE, "fixtures", "benign")
SCOPE_PARTITION = os.path.join(HERE, "fixtures", "scope-partition")


def _toolchain_ok() -> bool:
    if not sys.platform.startswith("linux"):
        return False
    return shutil.which("gcc") is not None and shutil.which("ar") is not None


requires_elf = pytest.mark.skipif(
    not _toolchain_ok(),
    reason="needs a Linux/ELF toolchain (gcc, ar); run inside the container",
)


def _build(script_dir: str, out: str):
    subprocess.run(["bash", os.path.join(script_dir, "build.sh"), out],
                   check=True, capture_output=True, text=True)


@pytest.fixture(scope="session")
def gt_dir(tmp_path_factory):
    if not _toolchain_ok():
        pytest.skip("no ELF toolchain")
    out = str(tmp_path_factory.mktemp("gt"))
    _build(SPLIT_STATE, out)
    return out


@pytest.fixture(scope="session")
def bn_dir(tmp_path_factory):
    if not _toolchain_ok():
        pytest.skip("no ELF toolchain")
    out = str(tmp_path_factory.mktemp("bn"))
    _build(BENIGN, out)
    return out


@pytest.fixture(scope="session")
def sp_dir(tmp_path_factory):
    if not _toolchain_ok():
        pytest.skip("no ELF toolchain")
    out = str(tmp_path_factory.mktemp("sp"))
    _build(SCOPE_PARTITION, out)
    return out


def analyze(exe, ld_library_path=None, modules=None, rtld_global=False,
           module_groups=None, assume_rtld_local=False):
    """Run the full pipeline and return (findings, has_split)."""
    from symsplit.allowlist import Allowlist
    from symsplit.analyze import Analyzer
    from symsplit.closure import resolve_closure
    from symsplit.model import Verdict

    cl = resolve_closure(exe, ld_library_path=ld_library_path or [],
                         extra_modules=modules or [], rtld_global=rtld_global,
                         module_groups=module_groups or {})
    findings = Analyzer(cl, Allowlist.load(),
                        assume_rtld_local=assume_rtld_local).run()
    has_split = any(f.verdict in Verdict.SPLIT_VERDICTS for f in findings)
    return findings, has_split


def verdict_of(findings, symbol):
    for f in findings:
        if f.symbol == symbol:
            return f.verdict
    return None
