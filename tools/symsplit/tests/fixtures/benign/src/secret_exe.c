/* exe defines dupfn() as a normal GLOBAL function and calls it. It is NOT
 * exported (no -rdynamic) and the exe does not link the provider, so dupfn
 * stays GLOBAL in .symtab but is absent from .dynsym. When symsplit later
 * composes the provider in (via --module), the exe's copy cannot interpose
 * because it is not dynamic -> NOT-DYNAMIC-BENIGN. */
#include <stdio.h>
void dupfn(void){ printf("exe dupfn\n"); }
int main(void){ dupfn(); return 0; }
