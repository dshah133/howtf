# Scope-capture reproducer for split-state linking (the production topology).
#
# Reconstructs the incident's ACTUAL binding topology, not the -Bsymbolic-functions
# shortcut of ../local/split-state:
#
#   copy A  = libbundle.so       startup DT_NEEDED dep -> in the GLOBAL scope
#   copy B  = libregistryB.so.1  dlopen'd RTLD_LOCAL    -> names stay private
#   provider= libproviderB.so    DT_NEEDED on copy B; ctor calls register_driver
#                                as a plain extern import -> resolved GLOBAL-first
#                                -> captured by copy A
#   app     reads copy A directly (in-house) and copy B via dlvsym (NCCL)
#
# No -Bsymbolic-functions. No protected visibility. The split is produced by
# SCOPE alone -- global-before-local relocation lookup.
#
# The bug world (build/) and fix world (build-fixed/) are separate directories so
# neither clobbers the other's libbundle.so (their SONAME is identical).
#
#   make bug      # register writes copy A; NCCL (copy B) sees 0 devices
#   make fixed    # hide copy A's registrar; register writes copy B; NCCL sees the device
#   make trace    # LD_DEBUG=bindings proof: provider's register_driver -> libbundle
#   make evidence # capture everything into artifacts/

CC      := gcc
CFLAGS  := -O0 -g -fno-lto -fPIC
LDFLAGS := -Wl,-rpath,'$$ORIGIN'

.PHONY: bug fixed trace evidence clean

# ---------- BUG world: build/ , copy A exports register_driver globally ----------
build:
	mkdir -p build
build/libbundle.so: bundle.c verbs.h bundle.map | build
	$(CC) $(CFLAGS) -shared -Wl,-soname,libbundle.so -Wl,--version-script=bundle.map $(LDFLAGS) -o $@ bundle.c
build/libregistryB.so.1: registryB.c verbs.h registryB.map | build
	$(CC) $(CFLAGS) -shared -Wl,-soname,libregistryB.so.1 -Wl,--version-script=registryB.map $(LDFLAGS) -o $@ registryB.c -ldl
build/libproviderB.so: providerB.c verbs.h build/libregistryB.so.1
	$(CC) $(CFLAGS) -shared $(LDFLAGS) -o $@ providerB.c -Lbuild -l:libregistryB.so.1
build/app: app.c verbs.h build/libbundle.so build/libregistryB.so.1 build/libproviderB.so
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ app.c -Lbuild -lbundle -ldl

# ---------- FIX world: build-fixed/ , copy A localizes register_driver ----------
build-fixed:
	mkdir -p build-fixed
build-fixed/libbundle.so: bundle.c verbs.h bundle-fixed.map | build-fixed
	$(CC) $(CFLAGS) -shared -Wl,-soname,libbundle.so -Wl,--version-script=bundle-fixed.map $(LDFLAGS) -o $@ bundle.c
build-fixed/libregistryB.so.1: registryB.c verbs.h registryB.map | build-fixed
	$(CC) $(CFLAGS) -shared -Wl,-soname,libregistryB.so.1 -Wl,--version-script=registryB.map $(LDFLAGS) -o $@ registryB.c -ldl
build-fixed/libproviderB.so: providerB.c verbs.h build-fixed/libregistryB.so.1
	$(CC) $(CFLAGS) -shared $(LDFLAGS) -o $@ providerB.c -Lbuild-fixed -l:libregistryB.so.1
build-fixed/app: app.c verbs.h build-fixed/libbundle.so build-fixed/libregistryB.so.1 build-fixed/libproviderB.so
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ app.c -Lbuild-fixed -lbundle -ldl

bug: build/app
	@echo "=== BUG: copy A exports register_driver into the global scope ==="
	@build/app

fixed: build-fixed/app
	@echo "=== FIXED: copy A's register_driver localized; capture falls through to copy B ==="
	@build-fixed/app

trace: build/app
	@echo "=== LD_DEBUG=bindings: which copy does the provider's register_driver bind to? ==="
	@rm -f build/ld.trace.*
	@LD_DEBUG=bindings LD_DEBUG_OUTPUT=build/ld.trace build/app >/dev/null 2>&1 || true
	@grep -hE 'register_driver|get_device_list' build/ld.trace.* | sed -E 's#^ *[0-9]+:[[:space:]]+##; s#/home/[^ ]*/build/##g' | sort -u

evidence:
	@mkdir -p artifacts
	@{ echo "# uname"; uname -a; echo "# gcc"; gcc --version | head -1; \
	   echo "# ld"; ld --version | head -1; } > artifacts/00_toolchain.txt
	@$(MAKE) --no-print-directory bug   > artifacts/01_bug_run.txt   2>&1
	@$(MAKE) --no-print-directory fixed > artifacts/02_fixed_run.txt 2>&1
	@$(MAKE) --no-print-directory trace > artifacts/03_ld_bindings.txt 2>&1
	@{ echo "## libbundle.so (copy A) dynamic symbols"; \
	   readelf --dyn-syms --wide build/libbundle.so | grep -E 'register_driver|get_device_list'; \
	   echo; echo "## libregistryB.so.1 (copy B) dynamic symbols"; \
	   readelf --dyn-syms --wide build/libregistryB.so.1 | grep -E 'register_driver|get_device_list'; \
	   echo; echo "## libbundle-fixed.so: register_driver localized (absent from .dynsym)"; \
	   readelf --dyn-syms --wide build-fixed/libbundle.so | grep -E 'register_driver|get_device_list'; \
	 } > artifacts/04_symbols.txt 2>&1
	@echo "artifacts written:"; ls -1 artifacts/

clean:
	rm -rf build build-fixed artifacts
