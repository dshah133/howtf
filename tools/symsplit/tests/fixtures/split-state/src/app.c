/* app.c - the executable. It links libcollective.so, libverbs_shared.so, and
 * (in the buggy composition) a static copy of the verbs layer. Identical across
 * every binary in this demo; only the LINK COMPOSITION changes.
 */
#include <stdio.h>

void collective_run(void);

int main(void)
{
    printf("== app: running collective device discovery ==\n");
    collective_run();
    return 0;
}
