package com.parentalcontrol.app

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.os.Bundle
import android.text.Editable
import android.text.TextWatcher
import android.view.MotionEvent
import android.view.View
import android.view.inputmethod.InputMethodManager
import android.widget.Button
import android.widget.EditText
import android.widget.ImageView
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity

/**
 * Live screen view + remote control. Touchscreen mode: tap/drag act directly on the point you
 * touch. Trackpad mode: drag moves the cursor relatively, a short tap clicks where the cursor
 * already is - like a laptop trackpad. Reuses the single already-connected AgentClient.
 */
class ScreenViewActivity : AppCompatActivity(), AgentClient.Listener {

    private var client: AgentClient? = null
    private lateinit var screenImage: ImageView
    private lateinit var waitingText: TextView
    private lateinit var statusText: TextView
    private lateinit var modeBtn: TextView
    private lateinit var rightClickBtn: Button
    private lateinit var kbInput: EditText

    private var trackpadMode = false
    private var lastBitmapW = 0
    private var lastBitmapH = 0
    private var gotFrame = false

    private var downX = 0f
    private var downY = 0f
    private var lastX = 0f
    private var lastY = 0f
    private var moved = false

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.wrap(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_screen)

        screenImage = findViewById(R.id.screenImage)
        waitingText = findViewById(R.id.waitingText)
        statusText = findViewById(R.id.statusText)
        modeBtn = findViewById(R.id.modeBtn)
        rightClickBtn = findViewById(R.id.rightClickBtn)
        kbInput = findViewById(R.id.kbInput)

        findViewById<Button>(R.id.exitBtn).setOnClickListener { finish() }

        modeBtn.setOnClickListener {
            trackpadMode = !trackpadMode
            modeBtn.setText(if (trackpadMode) R.string.mode_trackpad else R.string.mode_touchscreen)
            rightClickBtn.visibility = if (trackpadMode) View.VISIBLE else View.GONE
        }

        rightClickBtn.setOnClickListener {
            client?.mouseDown(right = true)
            client?.mouseUp(right = true)
        }

        findViewById<Button>(R.id.keyboardBtn).setOnClickListener {
            kbInput.setText("")
            kbInput.requestFocus()
            val imm = getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager
            imm.showSoftInput(kbInput, InputMethodManager.SHOW_IMPLICIT)
        }

        var lastLen = 0
        kbInput.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
            override fun onTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
            override fun afterTextChanged(s: Editable?) {
                val text = s?.toString() ?: ""
                if (text.length > lastLen) {
                    client?.keyText(text.substring(lastLen))
                } else if (text.length < lastLen) {
                    repeat(lastLen - text.length) { client?.keySpecial("backspace") }
                }
                lastLen = text.length
            }
        })
        kbInput.setOnEditorActionListener { _, _, _ -> client?.keySpecial("enter"); true }

        setupTouch()

        client = AgentSession.client
        if (client == null) {
            statusText.setText(R.string.status_offline)
            return
        }
        client!!.setListener(this)
        client!!.screenStart()
    }

    @SuppressLint("ClickableViewAccessibility")
    private fun setupTouch() {
        screenImage.setOnTouchListener { _, event ->
            val bmp = (screenImage.drawable as? android.graphics.drawable.BitmapDrawable)?.bitmap
            when (event.action) {
                MotionEvent.ACTION_DOWN -> {
                    downX = event.x; downY = event.y
                    lastX = event.x; lastY = event.y
                    moved = false
                    if (!trackpadMode && bmp != null) {
                        normalizedPoint(event.x, event.y, bmp)?.let { (nx, ny) ->
                            client?.mouseMove(nx, ny)
                            client?.mouseDown()
                        }
                    }
                }
                MotionEvent.ACTION_MOVE -> {
                    val dx = event.x - lastX
                    val dy = event.y - lastY
                    if (kotlin.math.abs(event.x - downX) > 8 || kotlin.math.abs(event.y - downY) > 8) moved = true
                    if (trackpadMode) {
                        client?.mouseDelta((dx * 1.6).toDouble(), (dy * 1.6).toDouble())
                    } else if (bmp != null) {
                        normalizedPoint(event.x, event.y, bmp)?.let { (nx, ny) -> client?.mouseMove(nx, ny) }
                    }
                    lastX = event.x; lastY = event.y
                }
                MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                    if (trackpadMode) {
                        if (!moved) { client?.mouseDown(); client?.mouseUp() }
                    } else {
                        client?.mouseUp()
                    }
                }
            }
            true
        }
    }

    /** Maps a raw touch point to a 0..1 point on the actual remote screen, accounting for the
     * ImageView's fitCenter letterboxing (the bitmap rarely fills the view exactly). */
    private fun normalizedPoint(viewX: Float, viewY: Float, bmp: Bitmap): Pair<Double, Double>? {
        val vw = screenImage.width.toFloat()
        val vh = screenImage.height.toFloat()
        if (vw <= 0 || vh <= 0) return null
        val scale = minOf(vw / bmp.width, vh / bmp.height)
        val dispW = bmp.width * scale
        val dispH = bmp.height * scale
        val offX = (vw - dispW) / 2f
        val offY = (vh - dispH) / 2f
        val x = ((viewX - offX) / dispW).toDouble()
        val y = ((viewY - offY) / dispH).toDouble()
        if (x < 0 || x > 1 || y < 0 || y > 1) return null
        return x to y
    }

    override fun onFrame(data: ByteArray) {
        val bmp = BitmapFactory.decodeByteArray(data, 0, data.size) ?: return
        runOnUiThread {
            if (!gotFrame) {
                gotFrame = true
                waitingText.visibility = View.GONE
            }
            screenImage.setImageBitmap(bmp)
            statusText.setText(R.string.status_online)
        }
    }

    override fun onAuthResult(ok: Boolean) {}
    override fun onApps(apps: List<AppInfo>, blocked: Set<String>) {}
    override fun onBlocked(blocked: Set<String>) {}
    override fun onLockResult(ok: Boolean) {}
    override fun onLockState(locked: Boolean) {}
    override fun onUnlockResult(ok: Boolean) {}
    override fun onUninstalled() { runOnUiThread { finish() } }
    override fun onSites(sites: List<String>) {}

    override fun onDisconnected(reason: String) {
        runOnUiThread { statusText.setText(R.string.status_offline) }
    }

    override fun onDestroy() {
        client?.screenStop()
        super.onDestroy()
    }
}
