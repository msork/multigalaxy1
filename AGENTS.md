# Repository guidance

- Rust is the default language. Put portable logic in workspace crates and keep platform integration isolated.
- Use PowerPC assembly only for verified hooks/trampolines/ABI boundaries. Use C/C++ only where the Galaxy engine or Petari requires it; document each FFI boundary and isolate `unsafe` code.
- Prefer `no_std` only after verifying the Wii target, compiler backend, linker, and runtime requirements. Never claim a Rust Wii runtime builds until cross-compilation and link validation succeed.
- Do not invent game addresses, offsets, symbols, structures, or APIs. Record evidence and game revision with every hook.
- Do not commit game executables, disc images, extracted proprietary assets, secrets, or generated binaries.
- Specifications must precede milestone implementation. Keep acceptance criteria and evidence level current.
- Never claim gameplay, Dolphin boot, or online behavior from compilation or static analysis alone.
- Run only requested or necessary validation; document unavailable tools and unperformed tests.
