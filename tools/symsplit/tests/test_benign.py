"""Acceptance test 2: hand-built benign fixtures that share the duplicate
shape but must each return a specific NON-split verdict."""
import os

from conftest import analyze, requires_elf, verdict_of
from symsplit.model import Verdict


@requires_elf
def test_weak_override(bn_dir):
    d = os.path.join(bn_dir, "weak")
    findings, has_split = analyze(os.path.join(d, "app_weak"), ld_library_path=[d])
    assert not has_split
    assert verdict_of(findings, "plugin_hook") == Verdict.WEAK_PATTERN


@requires_elf
def test_versioned(bn_dir):
    d = os.path.join(bn_dir, "versioned")
    findings, has_split = analyze(os.path.join(d, "app_versioned"), ld_library_path=[d])
    assert not has_split
    assert verdict_of(findings, "api_call") == Verdict.VERSIONED_BENIGN


@requires_elf
def test_hidden(bn_dir):
    d = os.path.join(bn_dir, "hidden")
    findings, has_split = analyze(os.path.join(d, "app_hidden"), ld_library_path=[d])
    assert not has_split
    assert verdict_of(findings, "helper") == Verdict.HIDDEN_BENIGN


@requires_elf
def test_allowlisted_malloc(bn_dir):
    d = os.path.join(bn_dir, "allowlist")
    findings, has_split = analyze(os.path.join(d, "app_allowlist"), ld_library_path=[d])
    assert not has_split
    assert verdict_of(findings, "malloc") == Verdict.ALLOWLISTED


@requires_elf
def test_symtab_only_not_dynamic(bn_dir):
    """The extra copy is GLOBAL but lives only in .symtab -> cannot interpose.
    Provider composed in dlopen-style via --module."""
    d = os.path.join(bn_dir, "symtab")
    findings, has_split = analyze(os.path.join(d, "app_symtab"),
                                  modules=[os.path.join(d, "libprovider.so")])
    assert not has_split
    assert verdict_of(findings, "dupfn") == Verdict.NOT_DYNAMIC_BENIGN
