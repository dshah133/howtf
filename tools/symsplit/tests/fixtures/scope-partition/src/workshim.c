/* workshim.c -- a tiny stand-in for a vendored runtime library (think
 * libgomp) that keeps process-local mutable state behind a few strong,
 * default-visibility exports. Deliberately never calls itself internally
 * (no self-referencing relocations either way) -- the point of the Route B
 * (SCOPE-PARTITION) fixture is that NEITHER copy needs to self-bind at all;
 * duplicate mutable state emerges purely from being loaded into separate
 * RTLD_LOCAL namespaces, exactly like the real-world case (the same runtime
 * library bundled separately inside several wheels, each dlopen'd
 * RTLD_LOCAL, each getting its own copy of e.g. omp_get_num_threads /
 * omp_set_num_threads's shared state).
 */

static int wq_state = 0;

int wq_get_state(void) {
    return wq_state;
}

void wq_set_state(int v) {
    wq_state = v;
}

int wq_add(int a, int b) {
    return a + b;
}
