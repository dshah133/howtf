"""Host-independent unit tests for the allowlist (no ELF toolchain needed)."""
from symsplit.allowlist import Allowlist


def test_exact_and_prefix_matches():
    al = Allowlist.load()
    assert al.match("malloc") == "malloc"
    assert al.match("free") == "free"
    assert al.match("__cxa_throw") == "__cxa_*"
    assert al.match("__asan_report_load8") == "__asan_*"
    assert al.match("je_malloc") == "je_*"


def test_non_matches():
    al = Allowlist.load()
    assert al.match("vx_get_device_list") is None
    assert al.match("plugin_hook") is None
    assert al.match("random_symbol") is None
