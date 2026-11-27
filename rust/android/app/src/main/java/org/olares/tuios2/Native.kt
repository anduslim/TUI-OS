package org.olares.tuios2

import android.view.Surface

/** JNI bridge to the Rust core (rust/crates/tuios-android). */
object Native {
    init {
        System.loadLibrary("tuios_android")
    }

    // Key ids understood by the Rust side (crates/tuios-android/src/keys.rs).
    const val ENTER = 1
    const val TAB = 2
    const val BACKSPACE = 3
    const val ESCAPE = 4
    const val UP = 5
    const val DOWN = 6
    const val LEFT = 7
    const val RIGHT = 8
    const val HOME = 9
    const val END = 10
    const val PAGE_UP = 11
    const val PAGE_DOWN = 12
    const val INSERT = 13
    const val DELETE = 14
    const val F_BASE = 100

    /** (Re)connect to tuiosd with these settings. */
    @JvmStatic external fun start(url: String, token: String, fontPx: Float)
    @JvmStatic external fun surfaceChanged(surface: Surface, width: Int, height: Int)
    @JvmStatic external fun surfaceDestroyed()
    /** Full clean repaint (after waking from sleep). */
    @JvmStatic external fun redrawAll()
    @JvmStatic external fun key(id: Int, meta: Int)
    /** A character typed with Ctrl/Alt held; [codepoint] is unshifted. */
    @JvmStatic external fun chord(codepoint: Int, meta: Int)
    @JvmStatic external fun text(text: String)
    @JvmStatic external fun paste(text: String)
    @JvmStatic external fun tap(x: Float, y: Float)
    @JvmStatic external fun swipe(dy: Float)
    @JvmStatic external fun swipeEnd()
}
