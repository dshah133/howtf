// main.c
//
#include <unistd.h>

extern int add(int, int);

int foo() {
  return 88;
}

int main(void) {
    int a = add(5, 10);
    int c = foo() + a;
    sleep(60);
}
