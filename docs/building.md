# Building

Host tooling is a Cargo workspace. Install Rust/Cargo, then run `cargo test --workspace` and `cargo run -p multigalaxy -- <original.dol> <modified.dol>`. Both workspace tests and the local reference comparison have been run successfully with Rust 1.99.1.

Injected Wii runtime builds are not yet defined. Before claiming support, pin the Rust compiler and PPC backend, linker, ABI, runtime policy, and integration with the chosen SMG1 build system. Prefer a hybrid boundary if toolchain compatibility is incomplete. Never fetch game files automatically.
