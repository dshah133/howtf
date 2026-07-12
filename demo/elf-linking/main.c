// main.c — the entire demo program. The interesting part is what links it.
#include <unistd.h>

extern int add(int a, int b);

int main(void) {
    int sum = add(5, 10);
    sleep(60); /* keeps the process alive so we can read /proc/<pid>/maps */
    return sum;
}
