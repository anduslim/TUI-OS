package org.olares.tuios2

import android.app.Activity
import android.app.AlertDialog
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.text.InputType
import android.util.TypedValue
import android.view.KeyCharacterMap
import android.view.KeyEvent
import android.view.WindowInsets
import android.view.WindowInsetsController
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView

/**
 * TUI OS v2 on the Boox: a thin client for tuiosd on the Olares One.
 *
 * Settings (server URL, token, font size) are kept in SharedPreferences. They
 * can be set in the dialog (first launch, or Ctrl+Alt+S) or over adb, which
 * saves typing a long token:
 *
 *   adb shell am start -n org.olares.tuios2/.MainActivity \
 *       --es url ws://100.64.0.1:7420 --es token <token>
 */
class MainActivity : Activity() {

    private lateinit var view: TermView
    private val prefs by lazy { getSharedPreferences("tuios", Context.MODE_PRIVATE) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        view = TermView(this) { pasteClipboard() }
        setContentView(view)
        view.requestFocus()
        applyIntent(intent)
        if (prefs.getString("token", "").isNullOrEmpty()) showSettings() else connect()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        if (applyIntent(intent)) connect()
    }

    /** Saves url/token/font extras from an adb `am start`. Returns true if any. */
    private fun applyIntent(intent: Intent?): Boolean {
        val extras = intent?.extras ?: return false
        var changed = false
        val edit = prefs.edit()
        extras.getString("url")?.let { edit.putString("url", it); changed = true }
        extras.getString("token")?.let { edit.putString("token", it); changed = true }
        extras.getString("font")?.toFloatOrNull()?.let { edit.putFloat("fontSp", it); changed = true }
        edit.apply()
        return changed
    }

    private fun connect() {
        val url = prefs.getString("url", DEFAULT_URL) ?: DEFAULT_URL
        val token = prefs.getString("token", "") ?: ""
        val sp = prefs.getFloat("fontSp", DEFAULT_FONT_SP)
        val px = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_SP, sp, resources.displayMetrics)
        Native.start(url, token, px)
    }

    override fun onResume() {
        super.onResume()
        hideSystemBars()
        // One clean repaint after sleep clears E-ink ghosting.
        Native.redrawAll()
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) hideSystemBars()
    }

    private fun hideSystemBars() {
        window.insetsController?.let {
            it.hide(WindowInsets.Type.systemBars())
            it.systemBarsBehavior = WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        }
    }

    private fun showSettings() {
        val pad = (16 * resources.displayMetrics.density).toInt()
        val layout = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad, pad, pad)
        }
        fun field(label: String, value: String, type: Int): EditText {
            layout.addView(TextView(this).apply { text = label })
            val e = EditText(this).apply { setText(value); inputType = type; isSingleLine = true }
            layout.addView(e)
            return e
        }
        val url = field("Server (tuiosd)", prefs.getString("url", DEFAULT_URL) ?: DEFAULT_URL, InputType.TYPE_TEXT_VARIATION_URI)
        val token = field("Token", prefs.getString("token", "") ?: "", InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD)
        val font = field(
            "Font size (sp)",
            prefs.getFloat("fontSp", DEFAULT_FONT_SP).toString(),
            InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_FLAG_DECIMAL,
        )
        AlertDialog.Builder(this)
            .setTitle("TUI OS v2 settings")
            .setView(layout)
            .setPositiveButton("Connect") { _, _ ->
                prefs.edit()
                    .putString("url", url.text.toString().trim())
                    .putString("token", token.text.toString().trim())
                    .putFloat("fontSp", font.text.toString().toFloatOrNull() ?: DEFAULT_FONT_SP)
                    .apply()
                connect()
            }
            .setNegativeButton("Cancel") { _, _ -> if (!prefs.getString("token", "").isNullOrEmpty()) connect() }
            .show()
    }

    private fun pasteClipboard() {
        val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        val text = cm.primaryClip?.takeIf { it.itemCount > 0 }?.getItemAt(0)?.coerceToText(this)?.toString()
        if (!text.isNullOrEmpty()) Native.paste(text)
    }

    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        val code = event.keyCode
        // Leave volume and power keys to the system.
        if (code == KeyEvent.KEYCODE_VOLUME_UP || code == KeyEvent.KEYCODE_VOLUME_DOWN ||
            code == KeyEvent.KEYCODE_POWER
        ) return super.dispatchKeyEvent(event)

        // IMEs may deliver a whole string at once.
        if (event.action == KeyEvent.ACTION_MULTIPLE && code == KeyEvent.KEYCODE_UNKNOWN) {
            event.characters?.let { Native.text(it) }
            return true
        }
        if (event.action != KeyEvent.ACTION_DOWN) return true

        val meta = event.metaState
        val ctrl = event.isCtrlPressed
        val alt = event.isAltPressed
        val shift = event.isShiftPressed

        if ((ctrl && code == KeyEvent.KEYCODE_V) || (shift && code == KeyEvent.KEYCODE_INSERT)) {
            pasteClipboard(); return true
        }
        if (ctrl && alt && code == KeyEvent.KEYCODE_S) {
            showSettings(); return true
        }

        val special = when (code) {
            KeyEvent.KEYCODE_ENTER, KeyEvent.KEYCODE_NUMPAD_ENTER -> Native.ENTER
            KeyEvent.KEYCODE_TAB -> Native.TAB
            KeyEvent.KEYCODE_DEL -> Native.BACKSPACE
            // BACK never closes the app; it is Escape, as in v1.
            KeyEvent.KEYCODE_ESCAPE, KeyEvent.KEYCODE_BACK -> Native.ESCAPE
            KeyEvent.KEYCODE_DPAD_UP -> Native.UP
            KeyEvent.KEYCODE_DPAD_DOWN -> Native.DOWN
            KeyEvent.KEYCODE_DPAD_LEFT -> Native.LEFT
            KeyEvent.KEYCODE_DPAD_RIGHT -> Native.RIGHT
            KeyEvent.KEYCODE_MOVE_HOME -> Native.HOME
            KeyEvent.KEYCODE_MOVE_END -> Native.END
            KeyEvent.KEYCODE_PAGE_UP -> Native.PAGE_UP
            KeyEvent.KEYCODE_PAGE_DOWN -> Native.PAGE_DOWN
            KeyEvent.KEYCODE_INSERT -> Native.INSERT
            KeyEvent.KEYCODE_FORWARD_DEL -> Native.DELETE
            in KeyEvent.KEYCODE_F1..KeyEvent.KEYCODE_F12 -> Native.F_BASE + 1 + (code - KeyEvent.KEYCODE_F1)
            else -> 0
        }
        if (special != 0) {
            Native.key(special, meta); return true
        }

        if (ctrl || alt) {
            // Unshifted character for the chord (Ctrl+C, Alt+X, Ctrl+B for tmux).
            val cp = event.getUnicodeChar(0)
            if (cp != 0 && (cp and KeyCharacterMap.COMBINING_ACCENT) == 0) Native.chord(cp, meta)
            return true
        }
        if (code == KeyEvent.KEYCODE_SPACE) {
            Native.text(" "); return true
        }
        val cp = event.getUnicodeChar(meta)
        if (cp != 0 && (cp and KeyCharacterMap.COMBINING_ACCENT) == 0) {
            Native.text(String(Character.toChars(cp)))
            return true
        }
        return super.dispatchKeyEvent(event)
    }

    companion object {
        const val DEFAULT_URL = "ws://100.64.0.1:7420"
        const val DEFAULT_FONT_SP = 14f
    }
}
