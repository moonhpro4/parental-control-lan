package com.parentalcontrol.app

import android.app.AlertDialog
import android.content.Context
import android.text.Editable
import android.text.TextWatcher
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.recyclerview.widget.GridLayoutManager
import androidx.recyclerview.widget.RecyclerView

class MainActivity : AppCompatActivity(), AgentClient.Listener {

    private lateinit var client: AgentClient
    private lateinit var adapter: AppAdapter
    private val ui = Handler(Looper.getMainLooper())

    private lateinit var pairLayout: View
    private lateinit var controlLayout: View
    private lateinit var ipInput: EditText
    private lateinit var pinInput: EditText
    private lateinit var pairStatus: TextView
    private lateinit var connStatus: TextView

    private var wantConnection = false
    private var everAuthed = false
    private var apps = listOf<AppInfo>()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        client = AgentClient(this)

        pairLayout = findViewById(R.id.pairLayout)
        controlLayout = findViewById(R.id.controlLayout)
        ipInput = findViewById(R.id.ipInput)
        pinInput = findViewById(R.id.pinInput)
        pairStatus = findViewById(R.id.pairStatus)
        connStatus = findViewById(R.id.connStatus)

        val prefs = getSharedPreferences("pair", Context.MODE_PRIVATE)
        ipInput.setText(prefs.getString("ip", ""))
        pinInput.setText(prefs.getString("pin", ""))

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

        findViewById<Button>(R.id.connectBtn).setOnClickListener {
            val ip = ipInput.text.toString().trim()
            val pin = pinInput.text.toString().trim()
            if (ip.isEmpty() || !Regex("\\d{6}").matches(pin)) {
                pairStatus.text = getString(R.string.enter_ip_pin)
                return@setOnClickListener
            }
            prefs.edit().putString("ip", ip).putString("pin", pin).apply()
            startConnect()
        }
        findViewById<Button>(R.id.lockBtn).setOnClickListener {
            if (!client.lock()) toast(getString(R.string.not_connected))
        }
        findViewById<Button>(R.id.refreshBtn).setOnClickListener {
            if (!client.listApps()) toast(getString(R.string.not_connected))
        }
        findViewById<EditText>(R.id.searchInput).addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
            override fun onTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
            override fun afterTextChanged(s: Editable?) { adapter.setQuery(s?.toString() ?: "") }
        })
        findViewById<Button>(R.id.uninstallBtn).setOnClickListener {
            AlertDialog.Builder(this)
                .setTitle(getString(R.string.dlg_title))
                .setMessage(getString(R.string.dlg_msg))
                .setPositiveButton(getString(R.string.dlg_ok)) { _, _ -> if (!client.uninstall()) toast(getString(R.string.not_connected)) }
                .setNegativeButton(getString(R.string.dlg_cancel), null)
                .show()
        }
        findViewById<Button>(R.id.unpairBtn).setOnClickListener {
            wantConnection = false
            everAuthed = false
            client.close()
            prefs.edit().remove("pin").apply()
            pinInput.setText("")
            showPair("")
        }

        if (prefs.getString("ip", "").orEmpty().isNotEmpty() &&
            prefs.getString("pin", "").orEmpty().length == 6) startConnect()
    }

    private fun startConnect() {
        wantConnection = true
        everAuthed = false
        pairStatus.setTextColor(0xFF555555.toInt())
        pairStatus.text = getString(R.string.connecting)
        client.connect(ipInput.text.toString().trim(), pinInput.text.toString().trim())
    }

    private fun showPair(msg: String) {
        pairLayout.visibility = View.VISIBLE
        controlLayout.visibility = View.GONE
        pairStatus.setTextColor(0xFFC62828.toInt())
        pairStatus.text = msg
    }

    private fun toast(s: String) = Toast.makeText(this, s, Toast.LENGTH_SHORT).show()

    override fun onAuthResult(ok: Boolean) {
        runOnUiThread {
            if (ok) {
                everAuthed = true
                pairLayout.visibility = View.GONE
                controlLayout.visibility = View.VISIBLE
                connStatus.text = getString(R.string.connected_to, ipInput.text.toString())
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
                ui.postDelayed({
                    if (wantConnection) client.connect(
                        ipInput.text.toString().trim(), pinInput.text.toString().trim())
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
