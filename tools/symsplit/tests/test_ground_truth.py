"""Acceptance test 1 (the centerpiece): the four-configuration gating matrix.

symsplit must flag EXACTLY config B (-Bsymbolic-functions) as SPLIT and pass
the other three clean. This is the ground-truth reproducer, so if this test
regresses the tool's core claim is broken.
"""
import os

import pytest
from conftest import analyze, requires_elf, verdict_of
from symsplit.model import Verdict


@requires_elf
def test_config_B_is_the_only_split(gt_dir):
    results = {}
    for cfg in ("A", "B", "D2", "Ch"):
        exe = os.path.join(gt_dir, cfg, "app_" + cfg)
        _findings, has_split = analyze(exe, ld_library_path=[os.path.join(gt_dir, cfg)])
        results[cfg] = has_split
    assert results == {"A": False, "B": True, "D2": False, "Ch": False}, results


@requires_elf
def test_B_splits_on_reader_symbol(gt_dir):
    exe = os.path.join(gt_dir, "B", "app_B")
    findings, has_split = analyze(exe, ld_library_path=[os.path.join(gt_dir, "B")])
    assert has_split
    assert verdict_of(findings, "vx_get_device_list") == Verdict.SPLIT
    # the writer symbol is self-bound but has no external reader -> not a split
    assert verdict_of(findings, "vx_register_device") == Verdict.NO_SPLIT


@requires_elf
@pytest.mark.parametrize("cfg", ["A", "D2", "Ch"])
def test_clean_configs_have_no_split(cfg, gt_dir):
    exe = os.path.join(gt_dir, cfg, "app_" + cfg)
    findings, has_split = analyze(exe, ld_library_path=[os.path.join(gt_dir, cfg)])
    assert not has_split
    assert all(f.verdict != Verdict.SPLIT for f in findings)


@requires_elf
def test_A_dso_is_interposable_not_selfbound(gt_dir):
    """The reason A is clean: the DSO kept an interposable self-reference."""
    from symsplit.closure import resolve_closure
    from symsplit.model import SelfBind
    cl = resolve_closure(os.path.join(gt_dir, "A", "app_A"),
                         ld_library_path=[os.path.join(gt_dir, "A")])
    dso = [m for m in cl.modules if m.name == "libverbs_shared.so"][0]
    assert dso.selfbind_status() == SelfBind.NOT_SELF_BOUND


@requires_elf
def test_cli_exit_code(gt_dir):
    """Exit nonzero iff a SPLIT exists."""
    from symsplit.cli import run
    assert run([os.path.join(gt_dir, "B", "app_B"),
                "--ld-library-path", os.path.join(gt_dir, "B")]) == 2
    assert run([os.path.join(gt_dir, "A", "app_A"),
                "--ld-library-path", os.path.join(gt_dir, "A")]) == 0
