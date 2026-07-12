#include <stdio.h>
void helper(void){ printf("exe helper\n"); }
void provider_entry(void);
int main(void){ helper(); provider_entry(); return 0; }
