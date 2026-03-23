/*
 * Custom Python launcher for Android.
 * Uses dlopen() instead of static NEEDED dependency on libpython3.11.so.1.0.
 * This avoids Android 15's ELF validation error at install time while allowing
 * the actual Python library to be downloaded on demand.
 * LD_LIBRARY_PATH (set by YoutubeDL.kt) ensures dlopen finds the library.
 *
 * Compile with:
 *   aarch64-linux-android29-clang -o libpython.so python_launcher.c \
 *       -ldl -Wl,-z,max-page-size=16384 -fPIE -pie -s
 */
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>

typedef int (*Py_BytesMain_fn)(int, char**);
typedef int (*Py_Main_fn)(int, wchar_t**);
typedef wchar_t* (*Py_DecodeLocale_fn)(const char*, size_t*);

/* Try multiple Python sonames for compatibility */
static const char* python_libs[] = {
    "libpython3.11.so.1.0",
    "libpython3.11.so",
    "libpython3.so",
    NULL
};

int main(int argc, char* argv[]) {
    void* lib = NULL;

    for (int i = 0; python_libs[i] != NULL; i++) {
        lib = dlopen(python_libs[i], RTLD_NOW | RTLD_GLOBAL);
        if (lib) break;
    }

    if (!lib) {
        fprintf(stderr, "python_launcher: cannot load Python library: %s\n", dlerror());
        return 1;
    }

    /* Prefer Py_BytesMain (Python 3.8+) — avoids wchar_t conversion */
    Py_BytesMain_fn bytes_main = (Py_BytesMain_fn)dlsym(lib, "Py_BytesMain");
    if (bytes_main) {
        return bytes_main(argc, argv);
    }

    /* Fallback to Py_Main with locale conversion */
    Py_Main_fn py_main = (Py_Main_fn)dlsym(lib, "Py_Main");
    Py_DecodeLocale_fn decode = (Py_DecodeLocale_fn)dlsym(lib, "Py_DecodeLocale");
    if (!py_main) {
        fprintf(stderr, "python_launcher: Py_Main not found: %s\n", dlerror());
        return 1;
    }
    if (!decode) {
        fprintf(stderr, "python_launcher: Py_DecodeLocale not found: %s\n", dlerror());
        return 1;
    }

    wchar_t** wargv = (wchar_t**)malloc(sizeof(wchar_t*) * (argc + 1));
    for (int i = 0; i < argc; i++) {
        wargv[i] = decode(argv[i], NULL);
    }
    wargv[argc] = NULL;

    return py_main(argc, wargv);
}
