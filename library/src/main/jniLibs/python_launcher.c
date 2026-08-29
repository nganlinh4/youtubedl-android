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
#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>

typedef int (*Py_BytesMain_fn)(int, char**);
typedef int (*Py_Main_fn)(int, wchar_t**);
typedef wchar_t* (*Py_DecodeLocale_fn)(const char*, size_t*);

static void* load_private_library(const char* directory, const char* name, int flags) {
    if (!directory || !directory[0]) return dlopen(name, flags);
    size_t size = strlen(directory) + strlen(name) + 2;
    char* path = malloc(size);
    if (!path) return NULL;
    snprintf(path, size, "%s/%s", directory, name);
    void* library = dlopen(path, flags);
    free(path);
    return library;
}

static void preload_private_libraries(const char* directory) {
    if (!directory || !directory[0]) return;
    for (int pass = 0; pass < 4; pass++) {
        DIR* stream = opendir(directory);
        if (!stream) return;
        struct dirent* entry;
        while ((entry = readdir(stream)) != NULL) {
            const char* name = entry->d_name;
            size_t length = strlen(name);
            if (length < 4 || strcmp(name + length - 3, ".so") != 0) continue;
            if (strncmp(name, "libpython", 9) == 0) continue;
            load_private_library(directory, name, RTLD_LAZY | RTLD_GLOBAL);
        }
        closedir(stream);
    }
}

/* Try multiple Python sonames for compatibility */
static const char* python_libs[] = {
    "libpython3.13.so.1.0",
    "libpython3.13.so",
    "libpython3.12.so.1.0",
    "libpython3.12.so",
    "libpython3.11.so.1.0",
    "libpython3.11.so",
    "libpython3.so",
    NULL
};

int main(int argc, char* argv[]) {
    void* lib = NULL;
    const char* library_dir = getenv("SGT_PYTHON_LIBRARY_DIR");
    if (library_dir && library_dir[0]) setenv("LD_LIBRARY_PATH", library_dir, 1);
    preload_private_libraries(library_dir);

    for (int i = 0; python_libs[i] != NULL; i++) {
        lib = load_private_library(library_dir, python_libs[i], RTLD_NOW | RTLD_GLOBAL);
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
