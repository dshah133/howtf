/* provider defines a WEAK default; the exe provides a STRONG override. */
#include <stdio.h>
__attribute__((weak)) void plugin_hook(void){ printf("weak default hook\n"); }
