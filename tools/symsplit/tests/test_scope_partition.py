"""Acceptance tests for Route B (SCOPE-PARTITION) and same-library
clustering (--by-library).

Change 2: two auditwheel-style vendored copies of the SAME tiny library
(libworkshim-<hash>.so.1), composed via --module so each defaults to its own
isolated RTLD_LOCAL scope. Neither self-binds (no -Bsymbolic anywhere) --
Route B needs no self-binding at all: the isolation itself is the
mechanism. Every strong symbol they both define must come back
SCOPE-PARTITION, not SPLIT and not any benign verdict.

Change 3: the same fixture, run with --by-library, must collapse the three
colliding symbols (wq_get_state, wq_set_state, wq_add) into ONE cluster row
("copies=2 shared_symbols=3") instead of three separate per-symbol rows --
and must NOT merge in the unrelated libother.so.1.
"""
import os

from conftest import analyze, requires_elf, verdict_of
from symsplit.model import Verdict


def _mods(d):
    return [
        os.path.join(d, "libworkshim-aaaa1111.so.1"),
        os.path.join(d, "libworkshim-bbbb2222.so.1"),
    ]


@requires_elf
def test_scope_partition_default_module_isolation(sp_dir):
    """Two --module copies of the same library, no explicit grouping ->
    each defaults to its own isolated local scope -> SCOPE-PARTITION for
    every symbol they both define."""
    findings, has_split = analyze(
        os.path.join(sp_dir, "app_scope"), modules=_mods(sp_dir))
    for sym in ("wq_get_state", "wq_set_state", "wq_add"):
        v = verdict_of(findings, sym)
        assert v == Verdict.SCOPE_PARTITION, (sym, v)
        assert v != Verdict.SPLIT
    assert has_split


@requires_elf
def test_scope_partition_unrelated_module_not_flagged(sp_dir):
    """libother.so.1 exports a symbol nothing else defines -> no finding for
    it at all (sanity check that Route B doesn't over-fire)."""
    findings, _ = analyze(
        os.path.join(sp_dir, "app_scope"),
        modules=_mods(sp_dir) + [os.path.join(sp_dir, "libother.so.1")])
    assert verdict_of(findings, "other_probe") is None


@requires_elf
def test_module_group_unifies_scope(sp_dir):
    """--module-group tells the tool the two copies share ONE dlopen
    namespace (e.g. a consumer and its own bundled dependency) -> they are
    no longer isolated from each other -> NOT SCOPE-PARTITION."""
    a, b = _mods(sp_dir)
    groups = {os.path.basename(a): "shared", os.path.basename(b): "shared"}
    findings, has_split = analyze(
        os.path.join(sp_dir, "app_scope"), modules=[a, b], module_groups=groups)
    v = verdict_of(findings, "wq_get_state")
    assert v != Verdict.SCOPE_PARTITION
    assert not has_split


@requires_elf
def test_assume_rtld_local_flags_plain_linked_dups(sp_dir):
    """The SAME two vendored copies, but as ORDINARY DT_NEEDED links (not
    --module). By default that's one shared/global scope (no split -- and
    indeed nothing references them here). --assume-rtld-local tells the
    tool to isolate ordinarily-linked DSOs too, which turns the duplicate
    into a scope-partition finding."""
    exe = os.path.join(sp_dir, "app_scope_linked")
    findings_default, split_default = analyze(exe, ld_library_path=[sp_dir])
    assert verdict_of(findings_default, "wq_get_state") != Verdict.SCOPE_PARTITION
    assert not split_default

    findings_assumed, split_assumed = analyze(
        exe, ld_library_path=[sp_dir], assume_rtld_local=True)
    assert verdict_of(findings_assumed, "wq_get_state") == Verdict.SCOPE_PARTITION
    assert split_assumed


@requires_elf
def test_cli_by_library_collapses_symbol_rows(sp_dir, capsys):
    """--by-library must collapse the 3 colliding symbols into ONE cluster
    line ('copies=2 shared_symbols=3'), while the default per-symbol table
    shows 3 separate SCOPE-PARTITION rows for the same closure."""
    from symsplit.cli import run

    exe = os.path.join(sp_dir, "app_scope")
    argv = [exe] + sum((["--module", m] for m in _mods(sp_dir)), [])

    rc_default = run(argv)
    out_default = capsys.readouterr().out
    assert rc_default == 2
    assert out_default.count("SCOPE-PARTITION") == 3

    rc_lib = run(argv + ["--by-library"])
    out_lib = capsys.readouterr().out
    assert rc_lib == 2
    assert "CLUSTER" in out_lib
    assert "copies=2" in out_lib
    assert "shared_symbols=3" in out_lib
    # collapsed: ONE cluster line's verdict roll-up (not 3 per-symbol rows)
    assert out_lib.count("SCOPE-PARTITION") == 1


@requires_elf
def test_cluster_modules_groups_by_library_fingerprint(sp_dir):
    """Unit-level check of the clustering fingerprint itself: the two
    hash-suffixed workshim copies cluster together (soname prefix stripped
    of the auditwheel-style hash), libother.so.1 does not join them."""
    from symsplit.closure import resolve_closure
    from symsplit.cluster import multi_copy_clusters, soname_prefix

    assert soname_prefix("libworkshim-aaaa1111.so.1") == "libworkshim.so.1"
    assert soname_prefix("libworkshim-bbbb2222.so.1") == "libworkshim.so.1"

    cl = resolve_closure(
        os.path.join(sp_dir, "app_scope"),
        extra_modules=_mods(sp_dir) + [os.path.join(sp_dir, "libother.so.1")],
    )
    clusters = multi_copy_clusters(cl.modules)
    assert len(clusters) == 1
    c = clusters[0]
    assert c.prefix == "libworkshim.so.1"
    assert len(c.modules) == 2
    assert set(c.shared_symbols) == {"wq_get_state", "wq_set_state", "wq_add"}
