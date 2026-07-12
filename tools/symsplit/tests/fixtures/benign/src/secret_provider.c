/* DSO exports dupfn() in .dynsym. ping() deliberately does NOT call dupfn(),
 * so the linker has no reason to auto-export the exe's copy. */
#include <stdio.h>
void dupfn(void){ printf("dso dupfn\n"); }
void ping(void){ printf("dso ping\n"); }
