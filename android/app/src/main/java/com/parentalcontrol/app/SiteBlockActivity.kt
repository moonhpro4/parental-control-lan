package com.parentalcontrol.app

import android.content.Context
import android.os.Bundle
import android.view.LayoutInflater
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity

/** Type a domain, press Enter (or Add) to block it; tap the X next to a blocked site to unblock it. */
class SiteBlockActivity : AppCompatActivity(), AgentClient.Listener {

    private var client: AgentClient? = null
    private lateinit var siteInput: EditText
    private lateinit var siteList: LinearLayout

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.wrap(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_sites)

        siteInput = findViewById(R.id.siteInput)
        siteList = findViewById(R.id.siteList)

        findViewById<Button>(R.id.backBtn).setOnClickListener { finish() }
        findViewById<Button>(R.id.addSiteBtn).setOnClickListener { addSite() }
        siteInput.setOnEditorActionListener { _, _, _ -> addSite(); true }

        client = AgentSession.client
        if (client == null) {
            toast(getString(R.string.not_connected))
            finish()
            return
        }
        client!!.setListener(this)
        client!!.listSites()
    }

    private fun addSite() {
        val raw = siteInput.text.toString().trim()
        if (raw.isEmpty()) return
        if (client?.setSiteBlocked(raw, true) == true) {
            siteInput.setText("")
        } else {
            toast(getString(R.string.not_connected))
        }
    }

    private fun toast(s: String) = Toast.makeText(this, s, Toast.LENGTH_SHORT).show()

    override fun onSites(sites: List<String>) {
        runOnUiThread {
            siteList.removeAllViews()
            val inflater = LayoutInflater.from(this)
            for (site in sites.sorted()) {
                val row = inflater.inflate(R.layout.item_site, siteList, false)
                row.findViewById<TextView>(R.id.siteName).text = site
                row.findViewById<TextView>(R.id.siteRemove).setOnClickListener {
                    client?.setSiteBlocked(site, false)
                }
                siteList.addView(row)
            }
        }
    }

    override fun onAuthResult(ok: Boolean) {}
    override fun onApps(apps: List<AppInfo>, blocked: Set<String>) {}
    override fun onBlocked(blocked: Set<String>) {}
    override fun onLockResult(ok: Boolean) {}
    override fun onLockState(locked: Boolean) {}
    override fun onUnlockResult(ok: Boolean) {}
    override fun onUninstalled() { runOnUiThread { finish() } }
    override fun onFrame(data: ByteArray) {}
    override fun onDisconnected(reason: String) {
        runOnUiThread { toast(getString(R.string.reconnecting)) }
    }
}
