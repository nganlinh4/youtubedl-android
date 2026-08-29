/*
 * Termux Python retains a DT_NEEDED entry for libandroid-support.so even on
 * Android versions where those compatibility symbols are provided by Bionic.
 * Its package has no ELF SONAME, so private-path preloading cannot satisfy that
 * dependency. This named shim provides the compatibility identity while symbol
 * resolution continues through libc.
 */
__attribute__((visibility("default"))) void ytdlp_android_support_compat(void) {}
