# Mixed static/dynamic symbol-interposition lab ("split state").
#
# Two copies of the same "verbs" symbols are live at once: one statically linked
# into the executable, one inside libverbs_shared.so. A load-time constructor
# registers devices into one copy's private table; a dynamically-linked
# collective does discovery against a copy. When registration and discovery bind
# to DIFFERENT copies, the collective sees an empty table -> "device not found"
# with the devices demonstrably registered. No linker error, no crash.
#
# The CENTERPIECE is `matrix`: a four-configuration gating experiment that pins
# EXACTLY when the split is real. A naive telling of this bug is wrong for
# default builds -- if the executable's static copy interposes globally, the
# DSO's own constructor ALSO binds to it, so no split occurs. The split needs
# BOTH: the executable exporting/interposing its copy AND the DSO self-binding
# its internal calls (e.g. -Bsymbolic-functions).
#
#   ./run.sh matrix            # the four-configuration gating experiment
#   ./run.sh bug               # the split (config B), headline repro
#   ./run.sh bug-b             # inverse: registration static, lookup shared
#   ./run.sh nondeterminism    # identical sources, one link token apart
#   ./run.sh fix-drop-duplicate fix-exclude-libs fix-prefix-rename
#   ./run.sh nofix-visibility nofix-version-script
#   ./run.sh evidence
#   ./run.sh all

CC     := gcc
B      := build
CF     := -O0 -g
CFPIC  := -O0 -g -fPIC
SRC    := src
RUN    := LD_LIBRARY_PATH=$(B)
DSHARED := -DVX_ORIGIN='"SHARED"' -DVX_WITH_CTOR
DSTATIC := -DVX_ORIGIN='"STATIC"'
WHOLE   := -Wl,--whole-archive -lverbs_static -Wl,--no-whole-archive

.PHONY: all matrix bug bug-b nondeterminism trigger-bsymbolic \
        fix-drop-duplicate fix-exclude-libs fix-prefix-rename \
        nofix-visibility nofix-version-script evidence clean $(B)

$(B):
	mkdir -p $(B)

# --- reusable inputs -------------------------------------------------------
$(B)/libcollective.so: $(SRC)/collective.c | $(B)
	$(CC) $(CFPIC) -shared $< -o $@
$(B)/libcollective_data.so: $(SRC)/collective_data.c | $(B)
	$(CC) $(CFPIC) -shared $< -o $@
$(B)/libverbs_static.a: $(SRC)/verbs.c | $(B)
	$(CC) $(CF) $(DSTATIC) -c $< -o $(B)/verbs_static.o
	ar crs $@ $(B)/verbs_static.o
$(B)/libverbs_static_ctor.a: $(SRC)/verbs.c | $(B)
	$(CC) $(CF) $(DSTATIC) -DVX_WITH_CTOR -c $< -o $(B)/verbs_static_ctor.o
	ar crs $@ $(B)/verbs_static_ctor.o

TR = grep -iE "constructor in|register ->|get_list <-|collective reads|discovered"

# ===========================================================================
# matrix: the gating experiment. Two copies of the verbs symbols exist (one in
# the exe from the static archive, one in the DSO). The split is real only when
# the constructor's writes and discovery's reads land on DIFFERENT copies --
# proven here by the TABLE ADDRESS printed on every register/lookup, not just a
# label. Four configurations per the referee's spec:
#   (A) default            (B) -Bsymbolic-functions
#   (C) DSO hidden/protected visibility   (D) data-object (table) collision
# ===========================================================================
matrix: $(B)/libcollective.so $(B)/libcollective_data.so $(B)/libverbs_static.a | $(B)
	@echo "############ (A) DEFAULT: DSO exports verbs fns, no special flags ############"
	@echo "# hypothesis: NO split -- the DSO constructor's register call is interposed onto the exe copy"
	$(CC) $(CFPIC) $(DSHARED) -shared $(SRC)/verbs.c -o $(B)/libverbs_shared.so
	$(CC) $(CF) $(SRC)/app.c -o $(B)/app_A -L$(B) -rdynamic -lcollective -lverbs_shared $(WHOLE)
	@$(RUN) ./$(B)/app_A 2>&1 | $(TR)
	@echo "# -> register and get_list print the SAME table@ address = same copy = no split"
	@echo
	@echo "############ (B) DSO -Bsymbolic-functions ############"
	@echo "# hypothesis: SPLIT -- constructor self-binds to the DSO copy; discovery reads the exe copy"
	$(CC) $(CFPIC) $(DSHARED) -Wl,-Bsymbolic-functions -shared $(SRC)/verbs.c -o $(B)/libverbs_shared.so
	$(CC) $(CF) $(SRC)/app.c -o $(B)/app_B -L$(B) -rdynamic -lcollective -lverbs_shared $(WHOLE)
	@$(RUN) ./$(B)/app_B 2>&1 | $(TR)
	@echo "# -> register table@ != get_list table@ = different copies = SPLIT"
	@echo
	@echo "############ (C) DSO internals given VISIBILITY (hidden / protected) ############"
	@echo "# C-protected: symbols still exported, DSO self-binds internally -> SPLIT"
	$(CC) $(CFPIC) $(DSHARED) -DVX_VIS_PROTECTED -shared $(SRC)/verbs.c -o $(B)/libverbs_shared.so
	$(CC) $(CF) $(SRC)/app.c -o $(B)/app_Cp -L$(B) -rdynamic -lcollective -lverbs_shared $(WHOLE)
	@$(RUN) ./$(B)/app_Cp 2>&1 | $(TR)
	@echo "# C-hidden: symbols NOT exported -> --as-needed DROPS the DSO, so the constructor never runs"
	$(CC) $(CFPIC) $(DSHARED) -DVX_VIS_HIDDEN -shared $(SRC)/verbs.c -o $(B)/libverbs_shared.so
	$(CC) $(CF) $(SRC)/app.c -o $(B)/app_Ch -L$(B) -rdynamic -lcollective -lverbs_shared $(WHOLE)
	@if readelf -d $(B)/app_Ch | grep -q "NEEDED.*libverbs_shared"; then echo "  (DSO retained)"; else echo "  >>> libverbs_shared.so DROPPED by --as-needed: constructor never runs (a DIFFERENT failure, not a split)"; fi
	@$(RUN) ./$(B)/app_Ch 2>&1 | $(TR)
	@echo "# C-hidden forced to load (--no-as-needed) -> the real SPLIT appears"
	$(CC) $(CF) $(SRC)/app.c -o $(B)/app_Ch2 -L$(B) -rdynamic -lcollective -Wl,--no-as-needed -lverbs_shared -Wl,--as-needed $(WHOLE)
	@$(RUN) ./$(B)/app_Ch2 2>&1 | $(TR)
	@echo
	@echo "############ (D) DATA-object collision: the device TABLE, not a function ############"
	@echo "# D1: DSO table file-local static, exe table GLOBAL, collective reads the table as DATA -> SPLIT"
	$(CC) $(CFPIC) $(DSHARED) -Wl,-Bsymbolic-functions -shared $(SRC)/verbs.c -o $(B)/libverbs_shared.so
	$(CC) $(CF) $(DSTATIC) -DVX_TABLE_GLOBAL -c $(SRC)/verbs.c -o $(B)/verbs_static_g.o; ar crs $(B)/libverbs_static_g.a $(B)/verbs_static_g.o
	$(CC) $(CF) $(SRC)/app.c -o $(B)/app_D1 -L$(B) -rdynamic -lcollective_data -Wl,--no-as-needed -lverbs_shared -Wl,--as-needed -Wl,--whole-archive -lverbs_static_g -Wl,--no-whole-archive
	@$(RUN) ./$(B)/app_D1 2>&1 | $(TR)
	@echo "# D2: table GLOBAL in BOTH exe and DSO -> COPY RELOCATION unifies them -> NO split"
	$(CC) $(CFPIC) $(DSHARED) -DVX_TABLE_GLOBAL -Wl,-Bsymbolic-functions -shared $(SRC)/verbs.c -o $(B)/libverbs_shared.so
	$(CC) $(CF) $(SRC)/app.c -o $(B)/app_D2 -L$(B) -rdynamic -lcollective_data -Wl,--no-as-needed -lverbs_shared -Wl,--as-needed -Wl,--whole-archive -lverbs_static_g -Wl,--no-whole-archive
	@$(RUN) ./$(B)/app_D2 2>&1 | $(TR)
	@echo "# -> in D2 the constructor's register table@ EQUALS the collective's read table@ (exe BSS copy)"
	@echo
	@echo "VERDICT: real split in B, C-protected, C-hidden(forced-load), D1. NOT in A (interposition"
	@echo "unifies), D2 (copy-relocation unifies), or C-hidden default (DSO dropped -> different failure)."

# bug: the split itself (config B), the headline reproduction.
bug: $(B)/libcollective.so $(B)/libverbs_static.a | $(B)
	$(CC) $(CFPIC) $(DSHARED) -Wl,-Bsymbolic-functions -shared $(SRC)/verbs.c -o $(B)/libverbs_shared.so
	$(CC) $(CF) $(SRC)/app.c -o $(B)/app_bug -L$(B) -rdynamic -lcollective -lverbs_shared $(WHOLE)
	@echo "### run (constructor fills the SHARED copy; discovery reads the STATIC copy):"
	@$(RUN) ./$(B)/app_bug

# bug-b: the inverse -- constructor in the static copy, discovery bound to the
# shared (empty) copy.
bug-b: $(B)/libcollective.so $(B)/libverbs_static_ctor.a | $(B)
	$(CC) $(CFPIC) -DVX_ORIGIN='"SHARED"' -shared $(SRC)/verbs.c -o $(B)/libverbs_shared_noctor.so
	$(CC) $(CF) $(SRC)/app.c -o $(B)/app_bug_b -L$(B) -lcollective -lverbs_shared_noctor \
	    -Wl,--whole-archive -lverbs_static_ctor -Wl,--no-whole-archive -Wl,--exclude-libs,ALL
	@echo "### run:"
	@$(RUN) ./$(B)/app_bug_b

# nondeterminism: identical app.c + identical .so's; the only difference is one
# token on the link line (whether the redundant static copy is linked).
nondeterminism: $(B)/libcollective.so $(B)/libverbs_static.a | $(B)
	$(CC) $(CFPIC) $(DSHARED) -Wl,-Bsymbolic-functions -shared $(SRC)/verbs.c -o $(B)/libverbs_shared.so
	$(CC) $(CF) $(SRC)/app.c -o $(B)/app_with_static    -L$(B) -rdynamic -lcollective -lverbs_shared $(WHOLE)
	$(CC) $(CF) $(SRC)/app.c -o $(B)/app_without_static -L$(B) -rdynamic -lcollective -lverbs_shared
	@echo "### app_with_static    (redundant static copy linked):"
	@$(RUN) ./$(B)/app_with_static    | grep discovered
	@echo "### app_without_static (single copy):"
	@$(RUN) ./$(B)/app_without_static | grep discovered

# trigger-bsymbolic: flip -Bsymbolic-functions on/off, same composition.
trigger-bsymbolic: $(B)/libcollective.so $(B)/libverbs_static.a | $(B)
	$(CC) $(CFPIC) $(DSHARED) -shared $(SRC)/verbs.c -o $(B)/libverbs_shared.so
	$(CC) $(CF) $(SRC)/app.c -o $(B)/app_nobsym -L$(B) -rdynamic -lcollective -lverbs_shared $(WHOLE)
	@echo "### DSO WITHOUT -Bsymbolic-functions:"; $(RUN) ./$(B)/app_nobsym | grep discovered
	$(CC) $(CFPIC) $(DSHARED) -Wl,-Bsymbolic-functions -shared $(SRC)/verbs.c -o $(B)/libverbs_shared.so
	$(CC) $(CF) $(SRC)/app.c -o $(B)/app_bsym -L$(B) -rdynamic -lcollective -lverbs_shared $(WHOLE)
	@echo "### DSO WITH -Bsymbolic-functions:"; $(RUN) ./$(B)/app_bsym | grep discovered

# --- fixes that work (verified) --------------------------------------------
fix-drop-duplicate: $(B)/libcollective.so | $(B)
	$(CC) $(CFPIC) $(DSHARED) -Wl,-Bsymbolic-functions -shared $(SRC)/verbs.c -o $(B)/libverbs_shared.so
	$(CC) $(CF) $(SRC)/app.c -o $(B)/app_fix_single -L$(B) -rdynamic -lcollective -lverbs_shared
	@echo "### run:"; $(RUN) ./$(B)/app_fix_single | grep discovered

fix-exclude-libs: $(B)/libcollective.so $(B)/libverbs_static.a | $(B)
	$(CC) $(CFPIC) $(DSHARED) -Wl,-Bsymbolic-functions -shared $(SRC)/verbs.c -o $(B)/libverbs_shared.so
	$(CC) $(CF) $(SRC)/app.c -o $(B)/app_fix_excl -L$(B) -rdynamic -lcollective -lverbs_shared $(WHOLE) -Wl,--exclude-libs,ALL
	@echo "### run:"; $(RUN) ./$(B)/app_fix_excl | grep discovered

fix-prefix-rename: $(B)/libverbs_static.a | $(B)
	$(CC) $(CFPIC) $(DSHARED) -shared $(SRC)/verbs.c -o $(B)/libverbs_shared_raw.so
	objcopy --redefine-sym vx_get_device_list=prov_vx_get_device_list \
	        --redefine-sym vx_register_device=prov_vx_register_device \
	        $(B)/libverbs_shared_raw.so $(B)/libverbs_shared_ns.so
	$(CC) $(CFPIC) -shared $(SRC)/collective.c -o $(B)/libcollective_raw.so
	objcopy --redefine-sym vx_get_device_list=prov_vx_get_device_list \
	        $(B)/libcollective_raw.so $(B)/libcollective_ns.so
	$(CC) $(CF) $(SRC)/app.c -o $(B)/app_fix_ns -L$(B) -rdynamic -l:libcollective_ns.so -l:libverbs_shared_ns.so $(WHOLE)
	@echo "### run:"; $(RUN) ./$(B)/app_fix_ns | grep discovered

# --- naive fixes that DO NOT work (honest negatives) -----------------------
# Both hide the DSO's symbols. Hidden symbols also make the DSO --as-needed
# droppable, so we force it to load (--no-as-needed) to show the SPLIT genuinely
# persists (constructor runs, fills the DSO copy; discovery still binds to the
# exe copy) rather than the DSO simply being dropped.
nofix-visibility: $(B)/libcollective.so $(B)/libverbs_static.a | $(B)
	$(CC) $(CFPIC) -fvisibility=hidden $(DSHARED) -Wl,-Bsymbolic-functions -shared $(SRC)/verbs.c -o $(B)/libverbs_shared.so
	$(CC) $(CF) $(SRC)/app.c -o $(B)/app_nofix_vis -L$(B) -rdynamic -lcollective -Wl,--no-as-needed -lverbs_shared -Wl,--as-needed $(WHOLE)
	@echo "### run (-fvisibility=hidden on the DSO, DSO forced to load -- still broken):"; $(RUN) ./$(B)/app_nofix_vis 2>&1 | grep -iE "register ->|get_list <-|discovered"

nofix-version-script: $(B)/libcollective.so $(B)/libverbs_static.a | $(B)
	$(CC) $(CFPIC) $(DSHARED) -Wl,-Bsymbolic-functions -Wl,--version-script=$(SRC)/hide-dso.map -shared $(SRC)/verbs.c -o $(B)/libverbs_shared.so
	$(CC) $(CF) $(SRC)/app.c -o $(B)/app_nofix_vs -L$(B) -rdynamic -lcollective -Wl,--no-as-needed -lverbs_shared -Wl,--as-needed $(WHOLE)
	@echo "### run (version-script local:* on the DSO, DSO forced to load -- still broken):"; $(RUN) ./$(B)/app_nofix_vs 2>&1 | grep -iE "register ->|get_list <-|discovered"

evidence: | $(B)
	bash evidence.sh

all: matrix bug bug-b nondeterminism trigger-bsymbolic \
     fix-drop-duplicate fix-exclude-libs fix-prefix-rename \
     nofix-visibility nofix-version-script

clean:
	rm -rf $(B)
