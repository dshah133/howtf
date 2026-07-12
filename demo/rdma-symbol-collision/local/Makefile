# Static-linking symbol-collision lab.
#
# Reproduces the silent trap where two static archives define the same strong
# C symbol (vx_open_device / vx_device_name) with different behavior, the linker
# satisfies the reference from whichever archive it scans first, and a later
# library that shipped its own copy silently runs the wrong one -- no
# "multiple definition" error ever fires.
#
# Run everything inside the Linux/GNU toolchain container (see ../run.sh):
#   make collision        # the silent bug: collective_b targets the WRONG NIC
#   make link-order       # reverse the link order: now collective_a is wrong
#   make explicit-error   # force both members to load -> real 'multiple def' error
#   make fixed-visibility # rebuild verbs with -fvisibility=hidden; test honestly
#   make fixed-objcopy    # objcopy --localize-symbol vs --redefine-sym; test
#   make fixed-groups     # --start-group/--end-group; test honestly
#   make all              # run every variant in sequence
#   make clean

CC      := gcc
AR      := ar
CFLAGS  := -O0 -g -fno-lto -ffunction-sections
SRC     := src
BUILD   := build

# GNU ld map/cref flags used to prove which archive member satisfied a symbol.
MAPFLAGS = -Wl,-Map=$(1) -Wl,--cref

.PHONY: all collision link-order explicit-error fixed-visibility fixed-objcopy fixed-groups \
        vendored-collision vendored-fixed-localize vendored-fixed-visibility clean archives

# ---------------------------------------------------------------------------
# Object files. One colliding symbol per object file -- THIS granularity is the
# precondition that lets the linker pull one archive's member while silently
# skipping the other archive's matching member.
# ---------------------------------------------------------------------------
$(BUILD):
	mkdir -p $(BUILD)

$(BUILD)/vendor_open.o:  $(SRC)/verbs_vendor_open.c  $(SRC)/verbs.h | $(BUILD)
	$(CC) $(CFLAGS) -c $< -o $@
$(BUILD)/vendor_name.o:  $(SRC)/verbs_vendor_name.c  $(SRC)/verbs.h | $(BUILD)
	$(CC) $(CFLAGS) -c $< -o $@
$(BUILD)/bundled_open.o: $(SRC)/verbs_bundled_open.c $(SRC)/verbs.h | $(BUILD)
	$(CC) $(CFLAGS) -c $< -o $@
$(BUILD)/bundled_name.o: $(SRC)/verbs_bundled_name.c $(SRC)/verbs.h | $(BUILD)
	$(CC) $(CFLAGS) -c $< -o $@
$(BUILD)/collective_a.o: $(SRC)/collective_a.c $(SRC)/verbs.h | $(BUILD)
	$(CC) $(CFLAGS) -c $< -o $@
$(BUILD)/collective_b.o: $(SRC)/collective_b.c $(SRC)/verbs.h | $(BUILD)
	$(CC) $(CFLAGS) -c $< -o $@

# Standard archives (each colliding symbol in its own member).
$(BUILD)/libverbs_vendor.a:   $(BUILD)/vendor_open.o  $(BUILD)/vendor_name.o
	$(AR) crs $@ $^
$(BUILD)/libverbs_bundled.a:  $(BUILD)/bundled_open.o $(BUILD)/bundled_name.o
	$(AR) crs $@ $^
$(BUILD)/libcollective_a.a:   $(BUILD)/collective_a.o
	$(AR) crs $@ $^
$(BUILD)/libcollective_b.a:   $(BUILD)/collective_b.o
	$(AR) crs $@ $^

archives: $(BUILD)/libverbs_vendor.a $(BUILD)/libverbs_bundled.a $(BUILD)/libcollective_a.a $(BUILD)/libcollective_b.a

# ---------------------------------------------------------------------------
# collision: the silent bug. Link order pairs each collective with its intended
# verbs archive, but first-definition-wins means the vendor copy (scanned first)
# satisfies EVERYONE's vx_open_device. collective_b's bundled copy is never
# pulled. No error. collective_b opens the wrong NIC.
# ---------------------------------------------------------------------------
collision: archives $(BUILD)/app.o
	@echo "### link: -lcollective_a -lverbs_vendor -lcollective_b -lverbs_bundled"
	$(CC) $(BUILD)/app.o -L$(BUILD) \
	    -lcollective_a -lverbs_vendor -lcollective_b -lverbs_bundled \
	    $(call MAPFLAGS,$(BUILD)/collision.map) -o $(BUILD)/app_collision
	@echo "### run:"
	@$(BUILD)/app_collision

$(BUILD)/app.o: $(SRC)/app.c | $(BUILD)
	$(CC) $(CFLAGS) -c $< -o $@

# ---------------------------------------------------------------------------
# link-order: reverse the two library pairs. Now the BUNDLED copy is scanned
# first, wins for everyone, and collective_a is the one misdirected.
# ---------------------------------------------------------------------------
link-order: archives $(BUILD)/app.o
	@echo "### link: -lcollective_b -lverbs_bundled -lcollective_a -lverbs_vendor"
	$(CC) $(BUILD)/app.o -L$(BUILD) \
	    -lcollective_b -lverbs_bundled -lcollective_a -lverbs_vendor \
	    $(call MAPFLAGS,$(BUILD)/link_order.map) -o $(BUILD)/app_link_order
	@echo "### run:"
	@$(BUILD)/app_link_order

# ---------------------------------------------------------------------------
# explicit-error: compile app.c with -DFORCE_PULL_BUNDLED so it references
# vx_bundled_probe, which is co-located with the bundled vx_open_device. That
# forces bundled_open.o to load, giving a SECOND vx_open_device definition ->
# real "multiple definition" error. Shows the conflict was always there; the
# silent case only stayed silent because that member was never pulled.
# Expected to FAIL the link; we capture and display the error.
# ---------------------------------------------------------------------------
explicit-error: archives | $(BUILD)
	$(CC) $(CFLAGS) -DFORCE_PULL_BUNDLED -c $(SRC)/app.c -o $(BUILD)/app_force.o
	@echo "### link (expected to fail with multiple definition):"
	@$(CC) $(BUILD)/app_force.o -L$(BUILD) \
	    -lcollective_a -lverbs_vendor -lcollective_b -lverbs_bundled \
	    -o $(BUILD)/app_explicit 2>&1 \
	    && echo "UNEXPECTED: link succeeded" \
	    || echo ">>> link failed as expected (see 'multiple definition' above)"

# ---------------------------------------------------------------------------
# fixed-visibility: rebuild the verbs objects with -fvisibility=hidden and
# hidden visibility attributes, then relink exactly as the collision case.
# Tests honestly whether hidden visibility changes static-archive resolution in
# an executable link. The runtime output tells the truth.
# ---------------------------------------------------------------------------
fixed-visibility: $(BUILD)/collective_a.o $(BUILD)/collective_b.o $(BUILD)/app.o \
                  $(BUILD)/libcollective_a.a $(BUILD)/libcollective_b.a | $(BUILD)
	$(CC) $(CFLAGS) -fvisibility=hidden -c $(SRC)/verbs_vendor_open.c  -o $(BUILD)/vendor_open_hidden.o
	$(CC) $(CFLAGS) -fvisibility=hidden -c $(SRC)/verbs_vendor_name.c  -o $(BUILD)/vendor_name_hidden.o
	$(CC) $(CFLAGS) -fvisibility=hidden -c $(SRC)/verbs_bundled_open.c -o $(BUILD)/bundled_open_hidden.o
	$(CC) $(CFLAGS) -fvisibility=hidden -c $(SRC)/verbs_bundled_name.c -o $(BUILD)/bundled_name_hidden.o
	$(AR) crs $(BUILD)/libverbs_vendor_hidden.a  $(BUILD)/vendor_open_hidden.o  $(BUILD)/vendor_name_hidden.o
	$(AR) crs $(BUILD)/libverbs_bundled_hidden.a $(BUILD)/bundled_open_hidden.o $(BUILD)/bundled_name_hidden.o
	@echo "### link with -fvisibility=hidden verbs:"
	@$(CC) $(BUILD)/app.o -L$(BUILD) \
	    -lcollective_a -lverbs_vendor_hidden -lcollective_b -lverbs_bundled_hidden \
	    $(call MAPFLAGS,$(BUILD)/fixed_visibility.map) -o $(BUILD)/app_fixed_visibility 2>&1 \
	    && { echo "### run:"; $(BUILD)/app_fixed_visibility; } \
	    || echo ">>> link failed"

# ---------------------------------------------------------------------------
# fixed-objcopy: two objcopy experiments, both reported honestly.
#   (1) --localize-symbol on the bundled archive: makes bundled's copies local.
#   (2) --redefine-sym namespacing on BOTH collective_b and its bundled verbs:
#       renames the pair so there is no shared global symbol at all.
# ---------------------------------------------------------------------------
fixed-objcopy: archives $(BUILD)/app.o | $(BUILD)
	@echo "===== experiment 1: objcopy --localize-symbol on libverbs_bundled.a ====="
	cp $(BUILD)/libverbs_bundled.a $(BUILD)/libverbs_bundled_localized.a
	objcopy --localize-symbol=vx_open_device --localize-symbol=vx_device_name \
	    $(BUILD)/libverbs_bundled_localized.a
	@echo "### link (bundled localized):"
	@$(CC) $(BUILD)/app.o -L$(BUILD) \
	    -lcollective_a -lverbs_vendor -lcollective_b -lverbs_bundled_localized \
	    -o $(BUILD)/app_localized 2>&1 \
	    && { echo "### run:"; $(BUILD)/app_localized; } \
	    || echo ">>> link failed"
	@echo
	@echo "===== experiment 2: objcopy --redefine-sym namespacing (collective_b + bundled) ====="
	cp $(BUILD)/libcollective_b.a $(BUILD)/libcollective_b_ns.a
	cp $(BUILD)/libverbs_bundled.a $(BUILD)/libverbs_bundled_ns.a
	objcopy --redefine-sym vx_open_device=vx_open_device__b \
	        --redefine-sym vx_device_name=vx_device_name__b $(BUILD)/libcollective_b_ns.a
	objcopy --redefine-sym vx_open_device=vx_open_device__b \
	        --redefine-sym vx_device_name=vx_device_name__b $(BUILD)/libverbs_bundled_ns.a
	@echo "### link (namespaced collective_b + bundled):"
	@$(CC) $(BUILD)/app.o -L$(BUILD) \
	    -lcollective_a -lverbs_vendor -lcollective_b_ns -lverbs_bundled_ns \
	    $(call MAPFLAGS,$(BUILD)/fixed_objcopy.map) -o $(BUILD)/app_namespaced 2>&1 \
	    && { echo "### run:"; $(BUILD)/app_namespaced; } \
	    || echo ">>> link failed"

# ---------------------------------------------------------------------------
# fixed-groups: wrap all four archives in --start-group/--end-group and test
# whether repeated archive rescanning changes the outcome. Reported honestly.
# ---------------------------------------------------------------------------
fixed-groups: archives $(BUILD)/app.o | $(BUILD)
	@echo "### link with --start-group ... --end-group:"
	@$(CC) $(BUILD)/app.o -L$(BUILD) \
	    -Wl,--start-group -lcollective_a -lverbs_vendor -lcollective_b -lverbs_bundled -Wl,--end-group \
	    $(call MAPFLAGS,$(BUILD)/fixed_groups.map) -o $(BUILD)/app_groups 2>&1 \
	    && { echo "### run:"; $(BUILD)/app_groups; } \
	    || echo ">>> link failed"

# ===========================================================================
# SCENARIO 2 -- vendored copy (def + use CO-LOCATED in one merged object).
#
# Scenario 1 above is the cross-library trap: the caller (collective_b) and the
# definition it should use (bundled archive) live in SEPARATE objects, so
# hidden visibility and --localize-symbol cannot help -- and the bundled member
# is never even pulled. Here each collective is merged with its OWN verbs copy
# into a single relocatable object via `ld -r`, matching how a big library
# statically vendors its own rdma-core. In THIS shape the collision is LOUD (a
# multiple-definition error, not silent) because the merged member carrying the
# entry point also carries vx_open_device -- and the localize/visibility fixes
# genuinely work, because the internal call is co-located with the definition.
# ===========================================================================
$(BUILD)/coll_a_vendored.o: $(BUILD)/collective_a.o $(BUILD)/vendor_open.o $(BUILD)/vendor_name.o
	ld -r $^ -o $@
$(BUILD)/coll_b_vendored.o: $(BUILD)/collective_b.o $(BUILD)/bundled_open.o $(BUILD)/bundled_name.o
	ld -r $^ -o $@

# vendored-collision: both merged objects keep a GLOBAL vx_open_device -> the
# final link sees two definitions and errors out loudly. (Contrast scenario 1's
# silent misdirection.)
vendored-collision: $(BUILD)/coll_a_vendored.o $(BUILD)/coll_b_vendored.o $(BUILD)/app.o
	$(AR) crs $(BUILD)/libcoll_a_vendored.a $(BUILD)/coll_a_vendored.o
	$(AR) crs $(BUILD)/libcoll_b_vendored.a $(BUILD)/coll_b_vendored.o
	@echo "### link (expected multiple-definition error -- the LOUD failure):"
	@$(CC) $(BUILD)/app.o -L$(BUILD) -lcoll_a_vendored -lcoll_b_vendored -o $(BUILD)/app_vendored 2>&1 \
	    && { echo "### run:"; $(BUILD)/app_vendored; } \
	    || echo ">>> link failed as expected (multiple definition)"

# vendored-fixed-localize: objcopy --localize-symbol on each merged object makes
# each library's vx_open_device LOCAL. The internal call (co-located) binds to
# the local copy; there is no shared global symbol. Bug gone.
vendored-fixed-localize: $(BUILD)/coll_a_vendored.o $(BUILD)/coll_b_vendored.o $(BUILD)/app.o
	cp $(BUILD)/coll_a_vendored.o $(BUILD)/coll_a_loc.o
	cp $(BUILD)/coll_b_vendored.o $(BUILD)/coll_b_loc.o
	objcopy --localize-symbol=vx_open_device --localize-symbol=vx_device_name $(BUILD)/coll_a_loc.o
	objcopy --localize-symbol=vx_open_device --localize-symbol=vx_device_name $(BUILD)/coll_b_loc.o
	$(AR) crs $(BUILD)/libcoll_a_loc.a $(BUILD)/coll_a_loc.o
	$(AR) crs $(BUILD)/libcoll_b_loc.a $(BUILD)/coll_b_loc.o
	@echo "### link (localized merged objects):"
	@$(CC) $(BUILD)/app.o -L$(BUILD) -lcoll_a_loc -lcoll_b_loc -o $(BUILD)/app_vloc 2>&1 \
	    && { echo "### run:"; $(BUILD)/app_vloc; } \
	    || echo ">>> link failed"

# vendored-fixed-visibility: compile the vendored verbs with -fvisibility=hidden,
# merge, then objcopy --localize-hidden to turn the hidden globals into locals.
# Bug gone. (Hidden visibility ALONE is not enough -- hidden globals still
# collide at static link; they must be localized.)
vendored-fixed-visibility: $(BUILD)/collective_a.o $(BUILD)/collective_b.o $(BUILD)/app.o
	$(CC) $(CFLAGS) -fvisibility=hidden -c $(SRC)/verbs_vendor_open.c  -o $(BUILD)/vo_h.o
	$(CC) $(CFLAGS) -fvisibility=hidden -c $(SRC)/verbs_vendor_name.c  -o $(BUILD)/vn_h.o
	$(CC) $(CFLAGS) -fvisibility=hidden -c $(SRC)/verbs_bundled_open.c -o $(BUILD)/bo_h.o
	$(CC) $(CFLAGS) -fvisibility=hidden -c $(SRC)/verbs_bundled_name.c -o $(BUILD)/bn_h.o
	ld -r $(BUILD)/collective_a.o $(BUILD)/vo_h.o $(BUILD)/vn_h.o -o $(BUILD)/coll_a_h.o
	ld -r $(BUILD)/collective_b.o $(BUILD)/bo_h.o $(BUILD)/bn_h.o -o $(BUILD)/coll_b_h.o
	objcopy --localize-hidden $(BUILD)/coll_a_h.o
	objcopy --localize-hidden $(BUILD)/coll_b_h.o
	$(AR) crs $(BUILD)/libcoll_a_h.a $(BUILD)/coll_a_h.o
	$(AR) crs $(BUILD)/libcoll_b_h.a $(BUILD)/coll_b_h.o
	@echo "### link (hidden + localize-hidden merged objects):"
	@$(CC) $(BUILD)/app.o -L$(BUILD) -lcoll_a_h -lcoll_b_h -o $(BUILD)/app_vh 2>&1 \
	    && { echo "### run:"; $(BUILD)/app_vh; } \
	    || echo ">>> link failed"

all: collision link-order explicit-error fixed-visibility fixed-objcopy fixed-groups \
     vendored-collision vendored-fixed-localize vendored-fixed-visibility

clean:
	rm -rf $(BUILD)
