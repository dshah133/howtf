"""Build and validate both numeric models, then refresh the captured files.

Needs Python 3 and a C11 compiler named cc (gcc or clang). Runs from any
directory; compiles with paths relative to this file so the captured compiler
warning carries no machine-specific path.
"""
import json
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CHECKS = [
    "Signed step preserved by the fixed ledger update",
    "Every tested negative step is off by 2^32 in the broken ledger update",
    "Published adjtime() value reproduced after 72 steps from any request of -500 to -1 us",
    "Published tick adjustment reproduced to within 1 ns/s",
    "Step invariant holds on the fixed path and fails on the first broken negative step",
    "Applied-plus-remaining conservation",
    "Closed-form finish-time oracle on the fixed path",
    "A step spread over 1, 7, 10, 100, or 1000 callbacks sums to the same step",
    "Four analytic application-delay integrals",
    "Event-aligned application matches immediate application",
    "Expected compiler sign-conversion diagnostic",
    "Ordinary UBSan does not diagnose this defined conversion",
]


def main():
    code = Path(__file__).resolve().parent
    compiler = shutil.which("cc")
    if not compiler:
        raise SystemExit("A C11 compiler named cc is required.")
    flags = ["-std=c11", "-O2", "-Wall", "-Wextra", "-Wconversion", "-Wsign-conversion"]
    with tempfile.TemporaryDirectory(prefix="clock-signed-slew-") as tmp:
        exe = str(Path(tmp) / "signed_clock")
        build = subprocess.run(
            [compiler, *flags, "signed_clock.c", "-o", exe],
            cwd=code, text=True, capture_output=True, check=True)
        assert "-Wsign-conversion" in build.stderr, build.stderr
        c = subprocess.run([exe], text=True, capture_output=True, check=True)
        assert not c.stderr
        py = subprocess.run(
            [sys.executable, "update_cadence.py"],
            cwd=code, text=True, capture_output=True, check=True)
        assert not py.stderr
        sanitizer_exe = str(Path(tmp) / "signed_clock_ubsan")
        subprocess.run(
            [compiler, "-std=c11", "-O1", "-fsanitize=undefined",
             "-fno-sanitize-recover=undefined", "signed_clock.c",
             "-o", sanitizer_exe],
            cwd=code, text=True, capture_output=True, check=True)
        ubsan = subprocess.run([sanitizer_exe], text=True, capture_output=True, check=True)
        assert ubsan.stdout == c.stdout
        assert not ubsan.stderr
    (code / "compiler-warning.txt").write_text(build.stderr)
    (code / "expected-output.txt").write_text(c.stdout + "\n" + py.stdout)
    record = {
        "compiler": subprocess.run([compiler, "--version"], capture_output=True,
                                   text=True, check=True).stdout.splitlines()[0],
        "python": platform.python_version(),
        "model_pass_line": c.stdout.strip().splitlines()[-1],
        "cadence_pass_line": py.stdout.strip().splitlines()[-1],
        "published_source": "ESXi 7.0 Update 3o release notes, PR 3246132; Broadcom KB 312016",
        "checks": CHECKS,
        "scope": "Numeric models only; no OS clock, ESXi, NIC, daemon, or migration test.",
        "ubsan_stderr": ubsan.stderr,
    }
    (code / "validation.json").write_text(json.dumps(record, indent=2) + "\n")
    print(c.stdout + py.stdout)
    print("Validation record written.")


if __name__ == "__main__":
    main()
