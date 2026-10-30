# Building

Host tooling is a Cargo workspace. Install Rust/Cargo, then run `cargo test --workspace` and `cargo run -p multigalaxy -- <original.dol> <modified.dol>`. Both workspace tests and the local reference comparison have been run successfully with Rust 1.99.1.

Revision inspection uses `cargo run -p multigalaxy -- identify <disc-header.bin> <main.dol>`, where the first file is the user's 0x80-byte raw Wii disc header. The Rust library parses PAL game ID/revision and matches the exact main.dol SHA-256 against registered maps. No verified PAL build maps are registered yet, so current inputs fail closed as unsupported. The library can register maps when their evidence is available; every hook must include original bytes and static verification before map validation accepts it.

Injected Wii runtime builds are not yet defined. Before claiming support, pin the Rust compiler and PPC backend, linker, ABI, runtime policy, and integration with the chosen SMG1 build system. Prefer a hybrid boundary if toolchain compatibility is incomplete. Never fetch game files automatically.
