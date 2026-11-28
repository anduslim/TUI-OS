//! Rust core of the Boox app.
//!
//! The Kotlin side (`android/`) owns the Activity, the SurfaceView, the
//! keyboard and touch events. It calls the `Native` JNI functions below; this
//! crate keeps the connection to `tuiosd`, the cell grid, and draws changed rows
//! straight into the Surface's buffer with only the dirty rectangle posted,
//! which keeps E-ink refreshes small.

pub mod keys;

#[cfg(target_os = "android")]
mod jni_api;
