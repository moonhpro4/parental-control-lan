package com.parentalcontrol.app

import android.app.AlertDialog
import android.content.Context
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.Editable
import android.text.TextWatcher
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.GridLayoutManager
import androidx.recyclerview.widget.RecyclerView
import kotlinx.coroutines.launch

class MainActivity : AppCompatActivity(), AgentClient.Listener {

    private lateinit var client: AgentClient
    private lateinit var adapter: AppAdapter
    private val ui = Handler(Looper.getMainLooper())

    private lateinit var pairLayout: View
    private lateinit var controlLayout: View
    private lateinit var pinInput: EditText
    private lateinit var ipInput: EditText
    private lateinit var manualToggle: TextView
    private lateinit var pairStatus: TextView
    private lateinit var findStatus: TextView
    private lateinit var connStatus: TextView
    private lateinit var langButton: TextView

    private var wantConnection = false
    private var everAuthed = false
    private var manualMode = false
    private var currentIp = ""
    private var apps = listOf<AppInfo>()

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.wrap(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        client = AgentClient(this)

        pairLayout = findViewById(R.id.pairLayout)
        controlLayout = findViewById(R.id.controlLayout)
        pinInput = findViewById(R.id.pinInput)
        ipInput = findViewById(R.id.ipInput)
        manualToggle = findViewById(R.id.manualToggle)
        pairStatus = findViewById(R.id.pairStatus)
        findStatus = findViewById(R.id.findStatus)
        connStatus = findViewById(R.id.connStatus)
        langButton = findViewById(R.id.langButton)

        val prefs = getSharedPreferences("pair", Context.MODE_PRIVATE)
        pinInput.setText(prefs.getString("pin", ""))
        ipInput.setText(prefs.getString("ip", ""))

        updateLangButton()
        langButton.setOnClickListener { showLanguagePicker() }

        manualToggle.setOnClickListener {
            manualMode = !manualMode
            ipInput.visibility = if (manualMode) View.VISIBLE else View.GONE
            manualToggle.setText(if (manualMode) R.string.btn_find_pc else R.string.btn_manual_ip)
        }

        adapter = AppAdapter { app, lock ->
            val exe = app.exe.lowercase()
            if (client.setBlocked(exe, lock)) {
                val b = adapter.blockedSet().toMutableSet()
                if (lock) b.add(exe) else b.remove(exe)
                adapter.submit(apps, b) // optimistic; server broadcast confirms
            } else toast(getString(R.string.not_connected))
        }
        findViewById<RecyclerView>(R.id.appGrid).apply {
            layoutManager = GridLayoutManager(this@MainActivity, 3)
            adapter = this@MainActivity.adapter
        }

        findViewById<EditText>(R.id.searchInput).addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
            override fun onTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
            override fun afterTextChanged(s: Editable?) { adapter.setQuery(s?.toString() ?: "") }
        })

        findViewById<Button>(R.id.connectBtn).setOnClickListener {
            val pin = pinInput.text.toString().trim()
            if (!Regex("\\d{6}").matches(pin)) {
                pairStatus.text = getString(R.string.enter_ip_pin)
                return@setOnClickListener
            }
            prefs.edit().putString("pin", pin).apply()
            if (manualMode) {
                val ip = ipInput.text.toString().trim()
                if (ip.isEmpty()) {
                    pairStatus.text = getString(R.string.enter_ip_pin)
                    return@setOnClickListener
                }
                prefs.edit().putString("ip", ip).apply()
                startConnect(ip, pin)
            } else {
                findThenConnect(pin)
            }
        }
        findViewById<Button>(R.id.lockBtn).setOnClickListener {
            if (!client.lock()) toast(getString(R.string.not_connected))
        }
        findViewById<Button>(R.id.unlockBtn).setOnClickListener {
            if (!client.unlock()) toast(getString(R.string.not_connected))
        }
        findViewById<Button>(R.id.refreshBtn).setOnClickListener {
            if (!client.listApps()) toast(getString(R.string.not_connected))
        }
        findViewById<Button>(R.id.uninstallBtn).setOnClickListener {
            AlertDialog.Builder(this)
                .setTitle(getString(R.string.dlg_title))
                .setMessage(getString(R.string.dlg_msg))
                .setPositiveButton(getString(R.string.dlg_ok)) { _, _ ->
                    if (!client.uninstall()) toast(getString(R.string.not_connected))
                }
                .setNegativeButton(getString(R.string.dlg_cancel), null)
                .show()
        }
        findViewById<Button>(R.id.unpairBtn).setOnClickListener {
            wantConnection = false
            everAuthed = false
            client.close()
            prefs.edit().remove("pin").remove("ip").apply()
            pinInput.setText("")
            ipInput.setText("")
            showPair("")
        }

        val savedPin = prefs.getString("pin", "").orEmpty()
        val savedIp = prefs.getString("ip", "").orEmpty()
        if (savedPin.length == 6) {
            if (savedIp.isNotEmpty()) startConnect(savedIp, savedPin) else findThenConnect(savedPin)
        }
    }

    private fun findThenConnect(pin: String) {
        findStatus.setTextColor(0xFFA6ABC9.toInt())
        findStatus.text = getString(R.string.finding_pc)
        lifecycleScope.launch {
            val ip = Discovery.findAgent()
            if (ip == null) {
                findStatus.setTextColor(0xFFFF5C6C.toInt())
                findStatus.text = getString(R.string.pc_not_found)
                return@launch
            }
            findStatus.setTextColor(0xFF4CD9A4.toInt())
            findStatus.text = getString(R.string.pc_found)
            getSharedPreferences("pair", Context.MODE_PRIVATE).edit().putString("ip", ip).apply()
            startConnect(ip, pin)
        }
    }

    private fun startConnect(ip: String, pin: String) {
        currentIp = ip
        wantConnection = true
        everAuthed = false
        pairStatus.setTextColor(0xFFA6ABC9.toInt())
        pairStatus.text = getString(R.string.connecting)
        client.connect(ip, pin)
    }

    private fun showPair(msg: String) {
        pairLayout.visibility = View.VISIBLE
        controlLayout.visibility = View.GONE
        pairStatus.setTextColor(0xFFFF5C6C.toInt())
        pairStatus.text = msg
    }

    private fun toast(s: String) = Toast.makeText(this, s, Toast.LENGTH_SHORT).show()

    private fun updateLangButton() {
        val code = LocaleHelper.saved(this) ?: "en"
        langButton.text = "\uD83C\uDF10 " + code.uppercase()
    }

    private fun showLanguagePicker() {
        val names = LocaleHelper.SUPPORTED.map { it.second }.toTypedArray()
        AlertDialog.Builder(this)
            .setTitle(R.string.choose_language)
            .setItems(names) { _, which ->
                LocaleHelper.save(this, LocaleHelper.SUPPORTED[which].first)
                recreate()
            }
            .show()
    }

    override fun onAuthResult(ok: Boolean) {
        runOnUiThread {
            if (ok) {
                everAuthed = true
                pairLayout.visibility = View.GONE
                controlLayout.visibility = View.VISIBLE
                connStatus.text = getString(R.string.connected_to, currentIp)
                client.listApps()
            } else {
                wantConnection = false
                showPair(getString(R.string.wrong_pin))
            }
        }
    }

    override fun onApps(apps: List<AppInfo>, blocked: Set<String>) {
        runOnUiThread { this.apps = apps; adapter.submit(apps, blocked) }
    }

    override fun onBlocked(blocked: Set<String>) {
        runOnUiThread { adapter.submit(apps, blocked) }
    }

    override fun onLockResult(ok: Boolean) {
        runOnUiThread { toast(getString(if (ok) R.string.pc_locked else R.string.lock_failed)) }
    }

    override fun onLockState(locked: Boolean) {
        runOnUiThread {
            findViewById<Button>(R.id.lockBtn).isEnabled = !locked
            findViewById<Button>(R.id.unlockBtn).isEnabled = locked
        }
    }

    override fun onUnlockResult(ok: Boolean) {
        runOnUiThread { if (ok) toast(getString(R.string.pc_unlocked)) }
    }

    override fun onUninstalled() {
        runOnUiThread {
            wantConnection = false
            everAuthed = false
            client.close()
            showPair(getString(R.string.uninstalled_msg))
        }
    }

    override fun onDisconnected(reason: String) {
        runOnUiThread {
            if (!wantConnection) return@runOnUiThread
            if (!everAuthed) {
                wantConnection = false
                showPair(getString(R.string.could_not_connect, reason))
            } else {
                connStatus.text = getString(R.string.reconnecting)
                val prefs = getSharedPreferences("pair", Context.MODE_PRIVATE)
                val ip = prefs.getString("ip", "").orEmpty()
                val pin = prefs.getString("pin", "").orEmpty()
                ui.postDelayed({
                    if (wantConnection) {
                        if (ip.isNotEmpty()) client.connect(ip, pin) else findThenConnect(pin)
                    }
                }, 2000)
            }
        }
    }

    override fun onDestroy() {
        wantConnection = false
        client.close()
        super.onDestroy()
    }
}
