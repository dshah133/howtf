# EC2 / real-rdma split-state: a load-time constructor enumerates the REAL rdma
# devices into one copy's registry, while the collective's discovery binds to
# the other (empty) copy -> the app reports "no rdma device" while ibv_devices
# shows them present. Requires rdma-core + two rxe devices (../../setup-rxe.sh).
#
#   make matrix   # four-configuration gating experiment on REAL devices
#   make bug      # the split (config B)
#   make fixed    # single canonical copy
#   make evidence # capture ibv state + nm + LD_DEBUG + runs into ../artifacts/
CC := gcc
B  := build
CF := -O0 -g
CFPIC := -O0 -g -fPIC
RUN := LD_LIBRARY_PATH=$(B)
DSHARED := -DVX_ORIGIN='"SHARED"' -DVX_WITH_CTOR
WHOLE := -Wl,--whole-archive -lverbs_static -Wl,--no-whole-archive
.PHONY: matrix bug fixed evidence clean $(B)

$(B):
	mkdir -p $(B)

$(B)/libcollective.so: rcollective.c | $(B)
	$(CC) $(CFPIC) -shared $< -o $@ -libverbs
$(B)/libverbs_static.a: rverbs.c | $(B)
	$(CC) $(CF) -DVX_ORIGIN='"STATIC"' -c $< -o $(B)/verbs_static.o
	ar crs $@ $(B)/verbs_static.o

# matrix: the same gating experiment as the local demo, but the constructor
# enumerates the REAL rxe devices. Split shows as "0 real rdma device(s)".
matrix: $(B)/libcollective.so $(B)/libverbs_static.a
	@echo "###### (A) default DSO + exe EXPORTS -> expect NO split ######"
	$(CC) $(CFPIC) $(DSHARED) -shared rverbs.c -o $(B)/libverbs_shared.so -libverbs
	$(CC) $(CF) rapp.c -o $(B)/rapp_A -L$(B) -rdynamic -lcollective -lverbs_shared $(WHOLE) -libverbs
	@$(RUN) ./$(B)/rapp_A 2>&1 | grep -iE "register ->|get_list <-|registry reports"
	@echo "###### (B) DSO -Bsymbolic-functions + exe EXPORTS -> expect SPLIT ######"
	$(CC) $(CFPIC) $(DSHARED) -Wl,-Bsymbolic-functions -shared rverbs.c -o $(B)/libverbs_shared.so -libverbs
	$(CC) $(CF) rapp.c -o $(B)/rapp_B -L$(B) -rdynamic -lcollective -lverbs_shared $(WHOLE) -libverbs
	@$(RUN) ./$(B)/rapp_B 2>&1 | grep -iE "register ->|get_list <-|registry reports"
	@echo "###### (C) DSO -Bsymbolic full + exe EXPORTS -> expect SPLIT ######"
	$(CC) $(CFPIC) $(DSHARED) -Wl,-Bsymbolic -shared rverbs.c -o $(B)/libverbs_shared.so -libverbs
	$(CC) $(CF) rapp.c -o $(B)/rapp_C -L$(B) -rdynamic -lcollective -lverbs_shared $(WHOLE) -libverbs
	@$(RUN) ./$(B)/rapp_C 2>&1 | grep -iE "register ->|get_list <-|registry reports"
	@echo "###### (D) DSO -Bsymbolic-functions + exe --exclude-libs,ALL -> expect NO split ######"
	$(CC) $(CFPIC) $(DSHARED) -Wl,-Bsymbolic-functions -shared rverbs.c -o $(B)/libverbs_shared.so -libverbs
	$(CC) $(CF) rapp.c -o $(B)/rapp_D -L$(B) -rdynamic -lcollective -lverbs_shared $(WHOLE) -Wl,--exclude-libs,ALL -libverbs
	@$(RUN) ./$(B)/rapp_D 2>&1 | grep -iE "register ->|get_list <-|registry reports"

bug: $(B)/libcollective.so $(B)/libverbs_static.a
	$(CC) $(CFPIC) $(DSHARED) -Wl,-Bsymbolic-functions -shared rverbs.c -o $(B)/libverbs_shared.so -libverbs
	$(CC) $(CF) rapp.c -o $(B)/rapp_bug -L$(B) -rdynamic -lcollective -lverbs_shared $(WHOLE) -libverbs
	@echo "### run (bug):"; $(RUN) ./$(B)/rapp_bug

# fixed: single canonical copy -- drop the redundant static archive.
fixed: $(B)/libcollective.so
	$(CC) $(CFPIC) $(DSHARED) -Wl,-Bsymbolic-functions -shared rverbs.c -o $(B)/libverbs_shared.so -libverbs
	$(CC) $(CF) rapp.c -o $(B)/rapp_fixed -L$(B) -rdynamic -lcollective -lverbs_shared -libverbs
	@echo "### run (fixed, single copy):"; $(RUN) ./$(B)/rapp_fixed

evidence: matrix bug fixed
	@mkdir -p ../artifacts
	{ uname -a; gcc --version|head -1; } > ../artifacts/00_toolchain.txt
	ibv_devices > ../artifacts/01_ibv_devices.txt 2>&1
	$(MAKE) --no-print-directory matrix > ../artifacts/02_matrix.txt 2>&1
	{ echo "# vx_get_device_list defined in BOTH the exe and the DSO:"; \
	  echo "## rapp_bug (exe):"; nm $(B)/rapp_bug | grep -E "vx_get_device_list|vx_register_device"; \
	  echo "## libverbs_shared.so:"; nm -D $(B)/libverbs_shared.so | grep -E "vx_get_device_list|vx_register_device"; } > ../artifacts/03_nm_duplicate_symbols.txt
	rm -f /tmp/ld.* 2>/dev/null || true
	LD_DEBUG=bindings LD_DEBUG_OUTPUT=/tmp/ld $(RUN) ./$(B)/rapp_bug >/dev/null 2>&1 || true
	cat /tmp/ld.* 2>/dev/null | grep -iE "vx_get_device_list|vx_register_device" | grep -i binding | sort -u > ../artifacts/04_ld_debug_bindings.txt || true
	$(RUN) ./$(B)/rapp_bug   > ../artifacts/05_bug_run.txt 2>&1
	$(RUN) ./$(B)/rapp_fixed > ../artifacts/06_fixed_run.txt 2>&1
	@echo "artifacts written to ../artifacts/"; ls -1 ../artifacts/

clean:
	rm -rf $(B)
