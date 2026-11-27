package org.olares.tuios2

import android.content.Context
import android.text.InputType
import android.view.MotionEvent
import android.view.SurfaceHolder
import android.view.SurfaceView
import android.view.ViewConfiguration
import android.view.inputmethod.BaseInputConnection
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputConnection
import android.view.inputmethod.InputMethodManager
import kotlin.math.abs

/**
 * The whole screen. Rust draws into this SurfaceView's buffer directly; this
 * class only forwards surface changes and touch gestures:
 *  - tap: click at that cell (tmux / k9s with mouse mode)
 *  - vertical swipe: scroll back through output
 *  - long press: paste the clipboard
 *  - two-finger tap: show or hide the on-screen keyboard
 */
class TermView(context: Context, private val onPaste: () -> Unit) :
    SurfaceView(context), SurfaceHolder.Callback {

    private val slop = ViewConfiguration.get(context).scaledTouchSlop
    private val longPressMs = 600L
    private var downX = 0f
    private var downY = 0f
    private var lastY = 0f
    private var downAt = 0L
    private var swiping = false
    private var multiTouch = false
    private var keyboardShown = false

    init {
        holder.addCallback(this)
        isFocusable = true
        isFocusableInTouchMode = true
    }

    override fun surfaceCreated(holder: SurfaceHolder) {}

    override fun surfaceChanged(holder: SurfaceHolder, format: Int, width: Int, height: Int) {
        Native.surfaceChanged(holder.surface, width, height)
    }

    override fun surfaceDestroyed(holder: SurfaceHolder) {
        Native.surfaceDestroyed()
    }

    // The soft keyboard sends raw key events (TYPE_NULL), which reach
    // MainActivity.dispatchKeyEvent like the keyboard cover's keys.
    override fun onCheckIsTextEditor() = true

    override fun onCreateInputConnection(outAttrs: EditorInfo): InputConnection {
        outAttrs.inputType = InputType.TYPE_NULL
        outAttrs.imeOptions = EditorInfo.IME_FLAG_NO_FULLSCREEN or EditorInfo.IME_FLAG_NO_EXTRACT_UI
        return BaseInputConnection(this, false)
    }

    private fun toggleKeyboard() {
        val imm = context.getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager
        keyboardShown = !keyboardShown
        if (keyboardShown) {
            requestFocus()
            imm.showSoftInput(this, InputMethodManager.SHOW_IMPLICIT)
        } else {
            imm.hideSoftInputFromWindow(windowToken, 0)
        }
    }

    override fun onTouchEvent(e: MotionEvent): Boolean {
        when (e.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                downX = e.x; downY = e.y; lastY = e.y
                downAt = e.eventTime
                swiping = false
                multiTouch = false
                requestFocus()
            }
            MotionEvent.ACTION_POINTER_DOWN -> multiTouch = true
            MotionEvent.ACTION_MOVE -> if (!multiTouch) {
                if (!swiping && abs(e.y - downY) > slop) swiping = true
                if (swiping) {
                    Native.swipe(e.y - lastY)
                    lastY = e.y
                }
            }
            MotionEvent.ACTION_UP -> {
                val held = e.eventTime - downAt
                when {
                    multiTouch -> toggleKeyboard()
                    swiping -> Native.swipeEnd()
                    abs(e.x - downX) > slop -> {}
                    held >= longPressMs -> onPaste()
                    else -> Native.tap(e.x, e.y)
                }
                swiping = false
            }
            MotionEvent.ACTION_CANCEL -> {
                swiping = false
                Native.swipeEnd()
            }
        }
        return true
    }
}
