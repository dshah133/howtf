#include <stdio.h>
#include <string.h>
#include "rverbs.h"
#ifndef VX_ORIGIN
#define VX_ORIGIN "?"
#endif
static const char *table[16];
static int count = 0;
void vx_register_device(const char *name){
  if(count < 16) table[count++] = name;
  fprintf(stderr, "    [register -> copy=%s] %s (this copy now holds %d)\n", VX_ORIGIN, name, count);
}
int vx_get_device_list(const char **out, int max){
  int n = count < max ? count : max;
  fprintf(stderr, "    [get_list <- copy=%s] this copy holds %d device(s)\n", VX_ORIGIN, count);
  for(int i=0;i<n;i++) out[i]=table[i];
  return n;
}
#ifdef VX_WITH_CTOR
#include <infiniband/verbs.h>
__attribute__((constructor)) static void vx_boot(void){
  int n=0; struct ibv_device **list=ibv_get_device_list(&n);
  fprintf(stderr, "    [constructor in copy=%s] enumerating %d REAL rdma device(s)\n", VX_ORIGIN, n);
  for(int i=0;i<n;i++) vx_register_device(strdup(ibv_get_device_name(list[i])));
  if(list) ibv_free_device_list(list);
}
#endif
