/* Sample C file with seeded defects for cppcheck integration tests.
 *
 * Contains a buffer overrun (arrayIndexOutOfBounds), an uninitialized
 * variable read (uninitvar), and a resource leak (memleak). These exercise
 * error-severity checks that cppcheck always runs.
 */
#include <stdlib.h>

int read_uninitialized(void) {
    int value;        /* never assigned */
    return value + 1; /* uninitvar: reading an uninitialized variable */
}

void write_out_of_bounds(void) {
    char buffer[5];
    buffer[10] = 'x'; /* arrayIndexOutOfBounds: index 10 into size-5 buffer */
}

int leak_memory(void) {
    int *data = (int *)malloc(sizeof(int) * 4);
    data[0] = 1;
    return data[0]; /* memleak: 'data' is never freed */
}
