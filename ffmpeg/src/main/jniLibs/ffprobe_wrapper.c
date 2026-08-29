#include <dlfcn.h>
#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef int (*main_fn)(int, char**);
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
static int is_shared_library(const char* name) {
    return strncmp(name, "lib", 3) == 0 && strstr(name, ".so") != NULL &&
        strcmp(name, "libffmpeg_real.so") != 0 &&
        strcmp(name, "libffprobe_real.so") != 0;
}
static void preload_private_libraries(const char* directory) {
    if (!directory || !directory[0]) return;
    for (int pass = 0; pass < 16; pass++) {
        DIR* stream = opendir(directory);
        if (!stream) return;
        struct dirent* entry;
        while ((entry = readdir(stream)) != NULL) {
            if (is_shared_library(entry->d_name))
                load_private_library(directory, entry->d_name, RTLD_NOW | RTLD_GLOBAL);
        }
        closedir(stream);
    }
}
int main(int argc, char* argv[]) {
    const char* library_dir = getenv("SGT_FFMPEG_LIBRARY_DIR");
    if (library_dir && library_dir[0]) setenv("LD_LIBRARY_PATH", library_dir, 1);
    preload_private_libraries(library_dir);
    void* bin = load_private_library(library_dir, "libffprobe_real.so", RTLD_NOW);
    if (!bin) { fprintf(stderr, "ffprobe_wrapper: %s\n", dlerror()); return 127; }
    main_fn m = (main_fn)dlsym(bin, "main");
    if (!m) { fprintf(stderr, "ffprobe_wrapper: main not found\n"); return 127; }
    return m(argc, argv);
}
