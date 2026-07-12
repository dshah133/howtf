/* a private hidden copy of helper() that must NOT be seen as an interposer */
#include <stdio.h>
__attribute__((visibility("hidden"))) void helper(void){ printf("hidden helper\n"); }
void provider_entry(void){ helper(); }
