/* a custom malloc in a DSO -- intentional interposer (allowlisted). */
#include <stddef.h>
#include <stdio.h>
static int inited;
void *malloc(size_t n){ inited=1; fprintf(stderr,"dso malloc %zu\n",n); return 0; }
__attribute__((constructor)) static void boot(void){ (void)malloc(1); }
