# Mixed static/dynamic symbol-interposition lab ("split state").
#
# Two copies of the same "verbs" symbols exist at once: one statically linked
# into the executable, one inside libverbs_shared.so. A constructor registers
# devices into one copy's private table while a dynamically-linked collective's
# discovery binds to the OTHER copy's (empty) table -> "device not found" with
# the device demonstrably registered. Run inside the toolchain container:
#
#   ./run.sh bug-a          # registration -> shared copy, lookup -> static (empty)
#   ./run.sh bug-b          # inverse: registration -> static, lookup -> shared (empty)
#   ./run.sh nondeterminism # identical sources, one link token apart: found vs not
#   ./run.sh trigger-bsymbolic
#   ./run.sh fix-drop-duplicate fix-exclude-libs fix-prefix-rename
#   ./run.sh nofix-visibility nofix-version-script   # honest negative results
#   ./run.sh evidence       # capture nm + LD_DEBUG bindings + runtime into artifacts/
#   ./run.sh all

CC     := gcc
B      := build
CF     := -O0 -g
CFPIC  := -O0 -g -fPIC
SRC    := src
RUN    := LD_LIBRARY_PATH=$(B)

# Two copies of the verbs layer, tagged so the runtime trace names the copy.
DSHARED := -DVX_ORIGIN='"SHARED"' -DVX_WITH_CTOR
DSTATIC := -DVX_ORIGIN='"STATIC"'

.PHONY: all bug-a bug-b nondeterminism trigger-bsymbolic \
        fix-drop-duplicate fix-exclude-libs fix-prefix-rename \
        nofix-visibility nofix-version-script evidence clean $(B)

$(B):
	mkdir -p $(B)

# --- reusable inputs -------------------------------------------------------
$(B)/libcollective.so: $(SRC)/collective.c | $(B)
	$(CC) $(CFPIC) -shared $< -o $@
$(B)/libverbs_static.a: $(SRC)/verbs.c | $(B)
	$(CC) $(CF) $(DSTATIC) -c $< -o $(B)/verbs_static.o
	ar crs $@ $(B)/verbs_static.o
$(B)/libverbs_static_ctor.a: $(SRC)/verbs.c | $(B)
	$(CC) $(CF) $(DSTATIC) -DVX_WITH_CTOR -c $< -o $(B)/verbs_static_ctor.o
	ar crs $@ $(B)/verbs_static_ctor.o

# libverbs_shared.so built plain (no self-binding) unless a target overrides it.
$(B)/libverbs_shared.so: $(SRC)/verbs.c | $(B)
	$(CC) $(CFPIC) $(DSHARED) -shared $< -o $@

WHOLE = -Wl,--whole-archive -lverbs_static -Wl,--no-whole-archive

# ---------------------------------------------------------------------------
# bug-a: the primary bug. libverbs_shared.so is built -Bsymbolic (a very common
# hardening/optimization flag), so its constructor self-binds and registers into
# the SHARED copy. The executable statically links a second copy and exports it
# (-rdynamic), so the collective's discovery binds to that STATIC (empty) copy.
# ---------------------------------------------------------------------------
bug-a: $(B)/libcollective.so $(B)/libverbs_static.a | $(B)
	$(CC) $(CFPIC) $(DSHARED) -Wl,-Bsymbolic -shared $(SRC)/verbs.c -o $(B)/libverbs_shared.so
	$(CC) $(CF) $(SRC)/app.c -o $(B)/app_bug_a -L$(B) -rdynamic -lcollective -lverbs_shared $(WHOLE)
	@echo "### run (stderr trace shows which copy registered vs was read):"
	@$(RUN) ./$(B)/app_bug_a

# ---------------------------------------------------------------------------
# bug-b: the inverse. The constructor lives in the STATIC copy (registers into
# it); the executable's static copy is hidden (--exclude-libs) so it does not
# export, so the collective binds to libverbs_shared.so's (empty) copy.
# ---------------------------------------------------------------------------
bug-b: $(B)/libcollective.so $(B)/libverbs_static_ctor.a | $(B)
	$(CC) $(CFPIC) -DVX_ORIGIN='"SHARED"' -shared $(SRC)/verbs.c -o $(B)/libverbs_shared_noctor.so
	$(CC) $(CF) $(SRC)/app.c -o $(B)/app_bug_b -L$(B) -lcollective -lverbs_shared_noctor \
	    -Wl,--whole-archive -lverbs_static_ctor -Wl,--no-whole-archive -Wl,--exclude-libs,ALL
	@echo "### run:"
	@$(RUN) ./$(B)/app_bug_b

# ---------------------------------------------------------------------------
# nondeterminism: TWO binaries from IDENTICAL app.c and IDENTICAL .so's. The
# only difference is one token on the link line: whether the redundant static
# copy is linked. One finds the devices, one does not.
# ---------------------------------------------------------------------------
nondeterminism: $(B)/libcollective.so $(B)/libverbs_static.a | $(B)
	$(CC) $(CFPIC) $(DSHARED) -Wl,-Bsymbolic -shared $(SRC)/verbs.c -o $(B)/libverbs_shared.so
	$(CC) $(CF) $(SRC)/app.c -o $(B)/app_with_static    -L$(B) -rdynamic -lcollective -lverbs_shared $(WHOLE)
	$(CC) $(CF) $(SRC)/app.c -o $(B)/app_without_static -L$(B) -rdynamic -lcollective -lverbs_shared
	@echo "### app_with_static    (redundant static copy linked):"
	@$(RUN) ./$(B)/app_with_static    | grep discovered
	@echo "### app_without_static (single copy):"
	@$(RUN) ./$(B)/app_without_static | grep discovered

# ---------------------------------------------------------------------------
# trigger-bsymbolic: same composition as bug-a but flip the DSO's -Bsymbolic on
# and off. -Bsymbolic is the TRIGGER here (not a fix): without it the ctor's
# register is interposed to the same static copy the collective reads (found);
# with it the ctor self-binds to the shared copy (not found).
# ---------------------------------------------------------------------------
trigger-bsymbolic: $(B)/libcollective.so $(B)/libverbs_static.a | $(B)
	$(CC) $(CFPIC) $(DSHARED) -shared $(SRC)/verbs.c -o $(B)/libverbs_shared.so
	$(CC) $(CF) $(SRC)/app.c -o $(B)/app_nobsym -L$(B) -rdynamic -lcollective -lverbs_shared $(WHOLE)
	@echo "### DSO WITHOUT -Bsymbolic:"; $(RUN) ./$(B)/app_nobsym | grep discovered
	$(CC) $(CFPIC) $(DSHARED) -Wl,-Bsymbolic -shared $(SRC)/verbs.c -o $(B)/libverbs_shared.so
	$(CC) $(CF) $(SRC)/app.c -o $(B)/app_bsym -L$(B) -rdynamic -lcollective -lverbs_shared $(WHOLE)
	@echo "### DSO WITH -Bsymbolic:"; $(RUN) ./$(B)/app_bsym | grep discovered

# ---------------------------------------------------------------------------
# FIXES that work (verified) ------------------------------------------------
# ---------------------------------------------------------------------------
# fix-drop-duplicate: single canonical copy -- do not statically link a second.
fix-drop-duplicate: $(B)/libcollective.so | $(B)
	$(CC) $(CFPIC) $(DSHARED) -Wl,-Bsymbolic -shared $(SRC)/verbs.c -o $(B)/libverbs_shared.so
	$(CC) $(CF) $(SRC)/app.c -o $(B)/app_fix_single -L$(B) -rdynamic -lcollective -lverbs_shared
	@echo "### run:"; $(RUN) ./$(B)/app_fix_single | grep discovered

# fix-exclude-libs: keep the static copy but stop the exe exporting it, so the
# collective binds to the shared copy (where the constructor registered).
fix-exclude-libs: $(B)/libcollective.so $(B)/libverbs_static.a | $(B)
	$(CC) $(CFPIC) $(DSHARED) -Wl,-Bsymbolic -shared $(SRC)/verbs.c -o $(B)/libverbs_shared.so
	$(CC) $(CF) $(SRC)/app.c -o $(B)/app_fix_excl -L$(B) -rdynamic -lcollective -lverbs_shared $(WHOLE) -Wl,--exclude-libs,ALL
	@echo "### run:"; $(RUN) ./$(B)/app_fix_excl | grep discovered

# fix-prefix-rename: namespace the shared provider's symbols + the collective's
# reference so the two copies are distinct symbols and cannot interpose.
fix-prefix-rename: $(B)/libverbs_static.a | $(B)
	$(CC) $(CFPIC) $(DSHARED) -shared $(SRC)/verbs.c -o $(B)/libverbs_shared_raw.so
	objcopy --redefine-sym vx_get_device_list=prov_vx_get_device_list \
	        --redefine-sym vx_register_device=prov_vx_register_device \
	        $(B)/libverbs_shared_raw.so $(B)/libverbs_shared.so
	$(CC) $(CFPIC) -shared $(SRC)/collective.c -o $(B)/libcollective_raw.so
	objcopy --redefine-sym vx_get_device_list=prov_vx_get_device_list \
	        $(B)/libcollective_raw.so $(B)/libcollective_ns.so
	$(CC) $(CF) $(SRC)/app.c -o $(B)/app_fix_ns -L$(B) -rdynamic -l:libcollective_ns.so -lverbs_shared $(WHOLE)
	@echo "### run:"; $(RUN) ./$(B)/app_fix_ns | grep discovered

# ---------------------------------------------------------------------------
# FIXES from the naive ladder that DO NOT work (verified honest negatives) ---
# Both hide the DSO's symbols, but the collective still binds to the exe's
# exported (empty) static copy, so devices are still not found.
# ---------------------------------------------------------------------------
nofix-visibility: $(B)/libcollective.so $(B)/libverbs_static.a | $(B)
	$(CC) $(CFPIC) -fvisibility=hidden $(DSHARED) -shared $(SRC)/verbs.c -o $(B)/libverbs_shared.so
	$(CC) $(CF) $(SRC)/app.c -o $(B)/app_nofix_vis -L$(B) -rdynamic -lcollective -lverbs_shared $(WHOLE)
	@echo "### run (-fvisibility=hidden on the DSO -- still broken):"; $(RUN) ./$(B)/app_nofix_vis | grep discovered

nofix-version-script: $(B)/libcollective.so $(B)/libverbs_static.a | $(B)
	$(CC) $(CFPIC) $(DSHARED) -Wl,--version-script=$(SRC)/hide-dso.map -shared $(SRC)/verbs.c -o $(B)/libverbs_shared.so
	$(CC) $(CF) $(SRC)/app.c -o $(B)/app_nofix_vs -L$(B) -rdynamic -lcollective -lverbs_shared $(WHOLE)
	@echo "### run (version-script local:* on the DSO -- still broken):"; $(RUN) ./$(B)/app_nofix_vs | grep discovered

# ---------------------------------------------------------------------------
evidence: | $(B)
	bash evidence.sh

all: bug-a bug-b nondeterminism trigger-bsymbolic \
     fix-drop-duplicate fix-exclude-libs fix-prefix-rename \
     nofix-visibility nofix-version-script

clean:
	rm -rf $(B)
