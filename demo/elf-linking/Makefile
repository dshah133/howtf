CC = gcc

all: libmath.so dynamic_app dynamic_app_lazy

libmath.so: math.c
	$(CC) -shared -fPIC -o libmath.so math.c

dynamic_app: main.c libmath.so
	$(CC) -o dynamic_app main.c -L. -lmath -Wl,-rpath,'$$ORIGIN'

# explicit lazy-binding variant for the PLT/GOT walkthrough (Part III)
dynamic_app_lazy: main.c libmath.so
	$(CC) -o dynamic_app_lazy main.c -L. -lmath -Wl,-z,lazy -Wl,-rpath,'$$ORIGIN'

clean:
	rm -f libmath.so dynamic_app dynamic_app_lazy dynamic_app_broken got_watch main.o
