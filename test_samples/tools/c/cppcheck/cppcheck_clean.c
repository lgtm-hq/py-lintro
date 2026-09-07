/* Sample clean C file that cppcheck should report no issues for. */
#include <stdlib.h>

int add_and_free(void) {
    int *data = (int *)malloc(sizeof(int));
    if (data == NULL) {
        return -1;
    }
    *data = 41;
    int result = *data + 1;
    free(data);
    return result;
}
