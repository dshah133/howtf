#!/usr/bin/env python3
"""Pilot analyzer for the split-state latent-hazard precondition in ML wheels.

Measures the PRECONDITION, not "bugs": strong global C-linkage symbols that are
DEFINED in two or more different .so's that can plausibly share one process
(different wheels co-imported into one Python). Also flags:
  - .so's built with -Bsymbolic / -Bsymbolic-functions (DF_SYMBOLIC / DF_1_..),
    the actual trigger that turns a duplicate into a silent split;
  - bundled duplicate copies of the SAME library (libgomp, libopenblas, ...).

Reads extracted wheels from /tmp/extract/<wheel>/..., writes report + raw TSVs
to /out (the mounted repo survey/pilot dir). Every number comes from nm/readelf.
"""
import os, re, subprocess, collections, json

EXTRACT = "/tmp/extract"
OUT = "/out"
MANGLED = re.compile(r"^_Z")          # skip C++ mangled names for the pilot
# nm type codes: uppercase = global/external. Strong-defined = T/D/B/R/A (text,
# data, bss, rodata, absolute). Weak = W/V/w/v (excluded). Local = lowercase.
STRONG = set("TDBRA")

def run(cmd, timeout=180):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""

def is_elf(p):
    try:
        with open(p, "rb") as fh:
            return fh.read(4) == b"\x7fELF"
    except Exception:
        return False

def find_sos(root):
    out = []
    for d, _, files in os.walk(root):
        for f in files:
            if f.endswith(".so") or ".so." in f:
                p = os.path.join(d, f)
                if is_elf(p):
                    out.append(p)
    return out

def strong_c_syms(so):
    syms = set()
    for line in run(["nm", "-D", "--defined-only", so]).splitlines():
        p = line.split()
        if len(p) < 3:
            continue
        typ, name = p[-2], p[-1]
        name = name.split("@")[0]   # normalize versioned symbols foo@@VER -> foo
        if name and len(typ) == 1 and typ in STRONG and not MANGLED.match(name):
            syms.add(name)
    return syms

def is_symbolic(so):
    d = run(["readelf", "-d", so])
    # DT_FLAGS SYMBOLIC or DT_FLAGS_1; readelf prints "SYMBOLIC" in the Flags line
    for line in d.splitlines():
        if ("FLAGS" in line or "FLAGS_1" in line) and "SYMBOLIC" in line:
            return True
    return False

def soname(so):
    for line in run(["readelf", "-d", so]).splitlines():
        if "SONAME" in line:
            m = re.search(r"\[(.+?)\]", line)
            if m:
                return m.group(1)
    return os.path.basename(so)

def libfamily(name):
    # normalize libfoo-<hash>.so.1.2 / libfoo.so.3 -> libfoo
    b = os.path.basename(name)
    b = re.sub(r"\.so.*$", "", b)
    b = re.sub(r"-[0-9a-f]{6,}$", "", b)   # auditwheel hash suffix
    b = re.sub(r"[._-]?\d[\d._]*$", "", b)  # trailing version
    return b

wheels = {}
for wd in sorted(os.listdir(EXTRACT)):
    wp = os.path.join(EXTRACT, wd)
    if os.path.isdir(wp):
        wheels[wd] = find_sos(wp)

# short wheel key (project name before first '-')
def wkey(wd):
    return wd.split("-")[0].lower()

sym_to_wheels = collections.defaultdict(set)   # symbol -> set(wheel-key)
sym_to_sos = collections.defaultdict(set)      # symbol -> set(so basename)
per_wheel = {}                                  # key -> stats
symbolic_sos = []                               # (wheel, so, soname)
family_to_wheels = collections.defaultdict(set)  # libfamily -> set(wheel-key)

for wd, sos in wheels.items():
    k = wkey(wd)
    tot_syms = set()
    nsym_so = 0
    for so in sos:
        syms = strong_c_syms(so)
        nsym_so += 1
        tot_syms |= syms
        for s in syms:
            sym_to_wheels[s].add(k)
            sym_to_sos[s].add(os.path.basename(so))
        if is_symbolic(so):
            symbolic_sos.append((k, os.path.basename(so), soname(so)))
        family_to_wheels[libfamily(so)].add(k)
    per_wheel[k] = dict(n_so=len(sos), n_strong_c=len(tot_syms))

# cross-wheel duplicate strong C symbols (defined in >=2 different wheels)
cross = {s: sorted(ws) for s, ws in sym_to_wheels.items() if len(ws) >= 2}

# classify into dangerous families by symbol-name prefix
FAMILIES = [
    ("OpenMP",      re.compile(r"^(GOMP_|omp_|__kmpc|kmp_|ompt_)")),
    ("BLAS/LAPACK", re.compile(r"(cblas_|_?lapacke?_|LAPACKE_|^s?gemm|openblas|goto)")),
    ("Fortran RT",  re.compile(r"^_gfortran_|^_quadmath|^__quadmath")),
    ("libstdc++",   re.compile(r"^__cxa_|^_ZSt|^__gnu_cxx")),  # mostly mangled-skipped
    ("zlib",        re.compile(r"^(inflate|deflate|crc32|adler32|gz|zlib)")),
    ("libpng/jpeg", re.compile(r"^(png_|jpeg_|jpeg\b|tjInit|WebP)")),
    ("protobuf-C",  re.compile(r"protobuf|upb_|google_protobuf")),
    ("OpenSSL",     re.compile(r"^(SSL_|EVP_|BIO_|RSA_|X509_|CRYPTO_)")),
]
fam_hits = collections.defaultdict(list)
for s, ws in cross.items():
    for fam, rx in FAMILIES:
        if rx.search(s):
            fam_hits[fam].append((s, ws))
            break

# bundled duplicate libraries (same family soname in >=2 wheels)
dup_libs = {f: sorted(ws) for f, ws in family_to_wheels.items() if len(ws) >= 2
            and re.search(r"lib(gomp|omp|openblas|blas|lapack|gfortran|quadmath|"
                          r"protobuf|stdc|gcc_s|z|png|jpeg|crypto|ssl|arrow|"
                          r"onnx|iomp|mkl)", f, re.I)}

os.makedirs(OUT, exist_ok=True)
with open(os.path.join(OUT, "raw_cross_wheel_dupes.tsv"), "w") as fh:
    fh.write("symbol\tn_wheels\twheels\tsos\n")
    for s in sorted(cross, key=lambda x: (-len(cross[x]), x)):
        fh.write(f"{s}\t{len(cross[s])}\t{','.join(cross[s])}\t{','.join(sorted(sym_to_sos[s]))}\n")
with open(os.path.join(OUT, "raw_symbolic_sos.tsv"), "w") as fh:
    fh.write("wheel\tso\tsoname\n")
    for k, so, sn in sorted(symbolic_sos):
        fh.write(f"{k}\t{so}\t{sn}\n")
with open(os.path.join(OUT, "raw_bundled_dup_libs.tsv"), "w") as fh:
    fh.write("lib_family\tn_wheels\twheels\n")
    for f in sorted(dup_libs, key=lambda x: -len(dup_libs[x])):
        fh.write(f"{f}\t{len(dup_libs[f])}\t{','.join(dup_libs[f])}\n")

# ---- report.md ----
def top(items, n=12):
    return items[:n]

R = []
R.append("# Pilot: split-state latent-hazard precondition in real ML wheels\n")
R.append(f"Wheels analyzed: {len(wheels)}  |  total .so files: {sum(len(v) for v in wheels.values())}\n")
R.append("## Per-wheel\n")
R.append("| wheel | #.so | #strong-global C syms | #-Bsymbolic .so |")
R.append("|---|---|---|---|")
symbolic_by_wheel = collections.Counter(k for k, _, _ in symbolic_sos)
for k in sorted(per_wheel):
    R.append(f"| {k} | {per_wheel[k]['n_so']} | {per_wheel[k]['n_strong_c']} | {symbolic_by_wheel.get(k,0)} |")

R.append("\n## Cross-wheel duplicate strong global C symbols (the precondition)\n")
R.append(f"Total distinct strong-global C symbols defined in >=2 different wheels: **{len(cross)}**\n")
R.append("### By dangerous family\n")
R.append("| family | # cross-wheel dup symbols | example symbols (wheels) |")
R.append("|---|---|---|")
for fam, _ in FAMILIES:
    hits = fam_hits.get(fam, [])
    if not hits:
        continue
    ex = "; ".join(f"`{s}` ({'+'.join(ws)})" for s, ws in top(sorted(hits), 3))
    R.append(f"| {fam} | {len(hits)} | {ex} |")

R.append("\n### Top cross-wheel duplicate symbols by breadth (most wheels sharing)\n")
R.append("| symbol | #wheels | wheels |")
R.append("|---|---|---|")
for s in top(sorted(cross, key=lambda x: (-len(cross[x]), x)), 20):
    R.append(f"| `{s}` | {len(cross[s])} | {', '.join(cross[s])} |")

R.append("\n## Bundled duplicate copies of the SAME library\n")
R.append("| lib family | #wheels | wheels |")
R.append("|---|---|---|")
for f in sorted(dup_libs, key=lambda x: -len(dup_libs[x])):
    R.append(f"| {f} | {len(dup_libs[f])} | {', '.join(dup_libs[f])} |")

R.append("\n## -Bsymbolic .so's (the trigger)\n")
R.append(f"Total .so's built with DF_SYMBOLIC: **{len(symbolic_sos)}**\n")
if symbolic_sos:
    R.append("| wheel | so | soname |")
    R.append("|---|---|---|")
    for k, so, sn in sorted(symbolic_sos)[:30]:
        R.append(f"| {k} | {so} | {sn} |")

# ---- verdict heuristic ----
danger = sum(len(fam_hits.get(f, [])) for f in ("OpenMP", "BLAS/LAPACK", "Fortran RT", "protobuf-C"))
n_dup_lib = len(dup_libs)
n_sym = len(symbolic_sos)
if danger >= 20 and n_dup_lib >= 3:
    verdict = "STRONG"
elif danger >= 5 or n_dup_lib >= 2:
    verdict = "THIN-TO-MODERATE"
else:
    verdict = "NULL"
R.append("\n## VERDICT\n")
R.append(f"**Signal: {verdict}.** dangerous-family cross-wheel dup symbols={danger}, "
         f"bundled duplicate lib families={n_dup_lib}, -Bsymbolic .so's={n_sym}.\n")
R.append("A duplicate strong symbol becomes a genuine latent split-state hazard when at least one "
         "defining .so is -Bsymbolic (see the gating experiment). Co-importing these wheels into one "
         "Python process is the real-world coexistence condition.\n")

with open(os.path.join(OUT, "report.md"), "w") as fh:
    fh.write("\n".join(R) + "\n")

print("\n".join(R))
print("\nraw TSVs + report.md written to", OUT)
