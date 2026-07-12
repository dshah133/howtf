#include <stdio.h>
#include <string.h>
#include <endian.h>
#include <infiniband/verbs.h>
#include "rverbs.h"
void collective_run(void){
  const char *names[16];
  int n = vx_get_device_list(names, 16);
  printf("  collective: registry reports %d rdma device(s)%s\n", n,
         n==0 ? "   *** NO DEVICE FOUND -- but the constructor enumerated the real devices into the OTHER copy ***" : "");
  int dn=0; struct ibv_device **list=ibv_get_device_list(&dn);
  for(int i=0;i<n;i++){
    struct ibv_context *ctx=NULL;
    for(int j=0;j<dn;j++) if(!strcmp(ibv_get_device_name(list[j]), names[i])){ ctx=ibv_open_device(list[j]); break; }
    if(ctx){ printf("    opened %-9s guid=%016llx\n", names[i], (unsigned long long)be64toh(ibv_get_device_guid(ctx->device))); ibv_close_device(ctx); }
  }
  if(list) ibv_free_device_list(list);
}
