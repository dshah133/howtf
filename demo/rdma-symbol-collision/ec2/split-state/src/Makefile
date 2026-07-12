# EC2 / real-rdma split-state: a load-time constructor enumerates the REAL rdma
# devices into one copy's registry, while the collective's discovery binds to
# the other (empty) copy -> the app reports "no rdma device" while ibv_devices
# shows them present. Requires rdma-core + two rxe devices (../../setup-rxe.sh).
CC := gcc
B  := build
CF := -O0 -g
CFPIC := -O0 -g -fPIC
RUN := LD_LIBRARY_PATH=$(B)
.PHONY: bug fixed evidence clean

$(B):
	mkdir -p $(B)

bug: | $(B)
	$(CC) $(CFPIC) -shared rcollective.c -o $(B)/libcollective.so -libverbs
	$(CC) $(CF) -DVX_ORIGIN='"STATIC"' -c rverbs.c -o $(B)/verbs_static.o
	ar crs $(B)/libverbs_static.a $(B)/verbs_static.o
	$(CC) $(CFPIC) -DVX_ORIGIN='"SHARED"' -DVX_WITH_CTOR -Wl,-Bsymbolic -shared rverbs.c -o $(B)/libverbs_shared.so -libverbs
	$(CC) $(CF) rapp.c -o $(B)/rapp_bug -L$(B) -rdynamic -lcollective -lverbs_shared \
	    -Wl,--whole-archive -lverbs_static -Wl,--no-whole-archive -libverbs
	@echo "### run (bug):"; $(RUN) ./$(B)/rapp_bug

# fixed: single canonical copy -- drop the redundant static archive.
fixed: | $(B)
	$(CC) $(CFPIC) -shared rcollective.c -o $(B)/libcollective.so -libverbs
	$(CC) $(CFPIC) -DVX_ORIGIN='"SHARED"' -DVX_WITH_CTOR -Wl,-Bsymbolic -shared rverbs.c -o $(B)/libverbs_shared.so -libverbs
	$(CC) $(CF) rapp.c -o $(B)/rapp_fixed -L$(B) -rdynamic -lcollective -lverbs_shared -libverbs
	@echo "### run (fixed, single copy):"; $(RUN) ./$(B)/rapp_fixed

evidence: bug fixed
	@mkdir -p ../artifacts
	{ uname -a; gcc --version|head -1; } > ../artifacts/00_toolchain.txt
	ibv_devices > ../artifacts/01_ibv_devices.txt 2>&1
	{ echo "# vx_get_device_list defined in BOTH the exe and the DSO:"; \
	  echo "## rapp_bug (exe):"; nm $(B)/rapp_bug | grep -E "vx_get_device_list|vx_register_device"; \
	  echo "## libverbs_shared.so:"; nm -D $(B)/libverbs_shared.so | grep -E "vx_get_device_list|vx_register_device"; } > ../artifacts/02_nm_duplicate_symbols.txt
	rm -f /tmp/ld.* 2>/dev/null || true
	LD_DEBUG=bindings LD_DEBUG_OUTPUT=/tmp/ld $(RUN) ./$(B)/rapp_bug >/dev/null 2>&1 || true
	cat /tmp/ld.* 2>/dev/null | grep -iE "vx_get_device_list|vx_register_device" | grep -i binding | sort -u > ../artifacts/03_ld_debug_bindings.txt || true
	$(RUN) ./$(B)/rapp_bug   > ../artifacts/04_bug_run.txt 2>&1
	$(RUN) ./$(B)/rapp_fixed > ../artifacts/05_fixed_run.txt 2>&1
	@echo "artifacts written to ../artifacts/"; ls -1 ../artifacts/

clean:
	rm -rf $(B)
