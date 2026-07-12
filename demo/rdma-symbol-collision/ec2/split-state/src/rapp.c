#include <stdio.h>
#include <infiniband/verbs.h>
void collective_run(void);
int main(void){
  int n=0; struct ibv_device **list=ibv_get_device_list(&n);
  printf("== real rdma devices present on this host (ibv_get_device_list): %d ==\n", n);
  for(int i=0;i<n;i++) printf("  [%d] %s\n", i, ibv_get_device_name(list[i]));
  if(list) ibv_free_device_list(list);
  printf("== app: collective discovery via the (split) verbs registry ==\n");
  collective_run();
  return 0;
}
