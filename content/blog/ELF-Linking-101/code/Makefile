all: libmath.so dynamic_app
libmath.so: math.c
	gcc -shared -fPIC -o libmath.so math.c
dynamic_app: main.c libmath.so
	gcc -o dynamic_app main.c ./libmath.so -Wl,-rpath,'$ORIGIN'
