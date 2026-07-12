# EC2 / real-rdma variant of the symbol-collision lab.
# The colliding symbol vx_select_device sits in front of REAL ibv_open_device.
# Requires: rdma-core, libibverbs-dev, and two soft-RoCE devices named
# rxe_train and rxe_store (see ../setup-rxe.sh).
#
#   make collision   # silent misdirection: collective_b opens the wrong REAL device
#   make fixed       # objcopy --redefine-sym namespacing -> bug gone
#   make evidence    # capture nm + link map + ibv state + runtime into artifacts/

CC     := gcc
AR     := ar
CFLAGS := -O0 -g -fno-lto
LDLIBS := -libverbs
B      := build

.PHONY: collision fixed evidence clean archives

$(B):
	mkdir -p $(B)

$(B)/rverbs_vendor.o:  rverbs_vendor.c  rverbs.h | $(B)
	$(CC) $(CFLAGS) -c $< -o $@
$(B)/rverbs_bundled.o: rverbs_bundled.c rverbs.h | $(B)
	$(CC) $(CFLAGS) -c $< -o $@
$(B)/rcollective_a.o:  rcollective_a.c rverbs.h rdma_util.h | $(B)
	$(CC) $(CFLAGS) -c $< -o $@
$(B)/rcollective_b.o:  rcollective_b.c rverbs.h rdma_util.h | $(B)
	$(CC) $(CFLAGS) -c $< -o $@
$(B)/rdma_util.o:      rdma_util.c rdma_util.h | $(B)
	$(CC) $(CFLAGS) -c $< -o $@
$(B)/rapp.o:           rapp.c | $(B)
	$(CC) $(CFLAGS) -c $< -o $@

$(B)/libverbs_vendor.a:  $(B)/rverbs_vendor.o
	$(AR) crs $@ $^
$(B)/libverbs_bundled.a: $(B)/rverbs_bundled.o
	$(AR) crs $@ $^
$(B)/libcollective_a.a:  $(B)/rcollective_a.o
	$(AR) crs $@ $^
$(B)/libcollective_b.a:  $(B)/rcollective_b.o
	$(AR) crs $@ $^

archives: $(B)/libverbs_vendor.a $(B)/libverbs_bundled.a $(B)/libcollective_a.a $(B)/libcollective_b.a

collision: archives $(B)/rapp.o $(B)/rdma_util.o
	@echo "### link: -lcollective_a -lverbs_vendor -lcollective_b -lverbs_bundled"
	$(CC) $(B)/rapp.o $(B)/rdma_util.o -L$(B) \
	    -lcollective_a -lverbs_vendor -lcollective_b -lverbs_bundled \
	    -Wl,-Map=$(B)/collision.map -Wl,--cref $(LDLIBS) -o $(B)/rapp_collision
	@echo "### run:"; $(B)/rapp_collision

# Namespacing fix that works for this cross-library layout: rename the colliding
# pair in BOTH collective_b and its bundled verbs so there is no shared global.
fixed: archives $(B)/rapp.o $(B)/rdma_util.o
	cp $(B)/libcollective_b.a $(B)/libcollective_b_ns.a
	cp $(B)/libverbs_bundled.a $(B)/libverbs_bundled_ns.a
	objcopy --redefine-sym vx_select_device=vx_select_device__b $(B)/libcollective_b_ns.a
	objcopy --redefine-sym vx_select_device=vx_select_device__b $(B)/libverbs_bundled_ns.a
	@echo "### link (namespaced collective_b + bundled):"
	$(CC) $(B)/rapp.o $(B)/rdma_util.o -L$(B) \
	    -lcollective_a -lverbs_vendor -lcollective_b_ns -lverbs_bundled_ns \
	    $(LDLIBS) -o $(B)/rapp_fixed
	@echo "### run:"; $(B)/rapp_fixed

evidence: collision fixed
	@mkdir -p ../artifacts
	{ echo "# uname"; uname -a; echo "# gcc"; gcc --version|head -1; echo "# ld"; ld --version|head -1; } > ../artifacts/00_toolchain.txt
	rdma link            > ../artifacts/01_rdma_link.txt 2>&1
	ibv_devices          > ../artifacts/02_ibv_devices.txt 2>&1
	ibv_devinfo -d rxe_train > ../artifacts/03_ibv_devinfo_train.txt 2>&1 || true
	ibv_devinfo -d rxe_store > ../artifacts/04_ibv_devinfo_store.txt 2>&1 || true
	{ echo "## libverbs_vendor.a"; nm $(B)/libverbs_vendor.a | grep vx_select_device; \
	  echo "## libverbs_bundled.a"; nm $(B)/libverbs_bundled.a | grep vx_select_device; } > ../artifacts/05_nm_duplicate_symbols.txt
	awk '/Archive member included/{p=1;print;next} p&&NF==0&&body{p=0} p{if(NF>0)body=1;print}' $(B)/collision.map > ../artifacts/06_collision_which_member.txt
	{ echo; echo "# member-pull proof (count of each verbs member in the link map):"; \
	  echo "#   rverbs_vendor.o  (vendor copy)  : $$(grep -c rverbs_vendor.o  $(B)/collision.map)  -> PULLED, satisfied vx_select_device for everyone"; \
	  echo "#   rverbs_bundled.o (bundled copy) : $$(grep -c rverbs_bundled.o $(B)/collision.map)  -> NEVER PULLED (0) -> silent misdirection, no error"; \
	} >> ../artifacts/06_collision_which_member.txt
	$(B)/rapp_collision  > ../artifacts/07_collision_run.txt 2>&1
	$(B)/rapp_fixed      > ../artifacts/08_fixed_run.txt 2>&1
	@echo "artifacts written to ../artifacts/"; ls -1 ../artifacts/

clean:
	rm -rf $(B)
