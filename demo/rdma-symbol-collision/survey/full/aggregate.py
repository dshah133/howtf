"""Aggregate per-unit results into the machine dataset + a ladder summary.

Reads results/static/*.json and results/coload/*.json, emits:
  dataset.json  - the full machine-readable survey record
  ladder.md     - the per-co-load-unit tier table + family breakdown (printed)
"""
import glob
import json
import os
import sys

ROOT = "/work/demo/rdma-symbol-collision/survey/full"
RES = os.path.join(ROOT, "results")


def load(kind):
    out = []
    for p in sorted(glob.glob(os.path.join(RES, kind, "*.json"))):
        if p.endswith(".maps.json"):
            continue
        try:
            out.append((os.path.basename(p)[:-5], json.load(open(p))))
        except Exception as e:                       # noqa: BLE001
            out.append((os.path.basename(p)[:-5], {"error": str(e)}))
    return out


def a_of(rec):
    return rec.get("analysis", rec)


def famsum(analyses):
    fam0, fam2 = {}, {}
    for a in analyses:
        for f, syms in a.get("families_tier0", {}).items():
            fam0.setdefault(f, set()).update(syms)
        for f, syms in a.get("families_tier2", {}).items():
            fam2.setdefault(f, set()).update(syms)
    return ({k: sorted(v) for k, v in fam0.items()},
            {k: sorted(v) for k, v in fam2.items()})


def main():
    static = load("static")
    coload = load("coload")

    dataset = {"static_units": {}, "coload_units": {}}
    for name, rec in static:
        dataset["static_units"][name] = rec
    for name, rec in coload:
        dataset["coload_units"][name] = rec
    json.dump(dataset, open(os.path.join(ROOT, "dataset.json"), "w"), indent=2)

    lines = []
    lines.append("## Co-load units (REALISTIC /proc/maps images) — the tier ladder\n")
    lines.append("| unit | imports | modules | Tier0 | Tier1 | Tier2 | verdicts |")
    lines.append("|---|---|---|---|---|---|---|")
    co_an = []
    n_units = n_with_split = 0
    for name, rec in coload:
        a = a_of(rec)
        if "n_modules" not in a:
            lines.append("| %s | ERROR | - | - | - | - | %s |" % (name, rec.get("error", rec.get("pip_error", "?"))[:40]))
            continue
        co_an.append(a)
        n_units += 1
        if a["tier2_count"] > 0:
            n_with_split += 1
        imp = ",".join(rec.get("imported", []))
        vc = a.get("verdict_counts", {})
        vcs = " ".join("%s=%d" % (k, v) for k, v in sorted(vc.items()))
        lines.append("| %s | %s | %d | %d | %d | **%d** | %s |" %
                     (name, imp, a["n_modules"], a["tier0_count"],
                      a["tier1_count"], a["tier2_count"], vcs))
    lines.append("")
    lines.append("**HEADLINE: %d of %d co-load units contain >=1 predicted SPLIT.**\n"
                 % (n_with_split, n_units))

    fam0, fam2 = famsum(co_an)
    lines.append("### Dangerous-family reach across co-load units")
    lines.append("| family | Tier0 symbols (dup, strong) | Tier2 symbols (SPLIT) |")
    lines.append("|---|---|---|")
    for fam in sorted(set(fam0) | set(fam2)):
        lines.append("| %s | %d | %d |" % (fam, len(fam0.get(fam, [])), len(fam2.get(fam, []))))
    lines.append("")

    lines.append("## Static units (per-wheel load groups + multi-wheel UNION upper bound)\n")
    lines.append("| unit | wheels | sos | modules | Tier0 | Tier1 | Tier2 |")
    lines.append("|---|---|---|---|---|---|---|")
    for name, rec in static:
        a = a_of(rec)
        if "n_modules" not in a:
            continue
        wl = ",".join(rec.get("wheels", []))[:40]
        lines.append("| %s | %s | %d | %d | %d | %d | **%d** |" %
                     (name, wl, rec.get("n_sos", 0), a["n_modules"],
                      a["tier0_count"], a["tier1_count"], a["tier2_count"]))
    lines.append("")

    # every Tier-2 finding, across all units, for adjudication
    lines.append("## All Tier-2 SPLIT findings (for adjudication)\n")
    total_t2 = 0
    for kind, units in (("coload", coload), ("static", static)):
        for name, rec in units:
            a = a_of(rec)
            for sp in a.get("tier2_splits", []):
                total_t2 += 1
                mods = ", ".join("%s(sb=%s)" % (c["module"], c["self_bind"])
                                 for c in sp["copies"])
                lines.append("- **[%s/%s]** `%s` sev=%s fam=%s\n  - copies: %s\n  - why: %s"
                             % (kind, name, sp["symbol"], sp["severity"],
                                sp.get("family"), mods, sp["why"]))
    if total_t2 == 0:
        lines.append("_None. Tier-2 is empty across every unit._")
    lines.append("\n**Total Tier-2 SPLIT findings across all units: %d**" % total_t2)

    md = "\n".join(lines)
    open(os.path.join(ROOT, "ladder.md"), "w").write(md)
    print(md)
    print("\n[dataset.json + ladder.md written]")
    print("HEADLINE_N_WITH_SPLIT=%d N_UNITS=%d TOTAL_T2=%d" %
          (n_with_split, n_units, total_t2))


if __name__ == "__main__":
    main()
