package com.parentalcontrol.app

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.core.content.ContextCompat
import androidx.recyclerview.widget.RecyclerView

class AppAdapter(private val onTap: (AppInfo, Boolean) -> Unit) :
    RecyclerView.Adapter<AppAdapter.VH>() {

    private var all = listOf<AppInfo>()
    private var items = listOf<AppInfo>()
    private var blocked = setOf<String>()
    private var query = ""

    fun submit(apps: List<AppInfo>, blockedSet: Set<String>) {
        all = apps
        blocked = blockedSet
        refilter()
    }

    fun setQuery(q: String) {
        query = q.trim().lowercase()
        refilter()
    }

    private fun refilter() {
        items = if (query.isEmpty()) all else all.filter {
            it.name.lowercase().contains(query) || it.exe.lowercase().contains(query)
        }
        notifyDataSetChanged()
    }

    fun blockedSet() = blocked

    class VH(v: View) : RecyclerView.ViewHolder(v) {
        val name: TextView = v.findViewById(R.id.appName)
        val exe: TextView = v.findViewById(R.id.appExe)
        val state: TextView = v.findViewById(R.id.appState)
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH =
        VH(LayoutInflater.from(parent.context).inflate(R.layout.item_app, parent, false))

    override fun getItemCount() = items.size

    override fun onBindViewHolder(h: VH, position: Int) {
        val app = items[position]
        val ctx = h.itemView.context
        val locked = blocked.contains(app.exe.lowercase())
        h.name.text = app.name
        h.exe.text = app.exe
        h.state.text = if (locked) "\u25CF LOCKED" else "\u2713 Unlocked"
        h.state.setTextColor(ContextCompat.getColor(ctx, if (locked) R.color.danger else R.color.success))
        h.itemView.setBackgroundResource(if (locked) R.drawable.card_bg_locked else R.drawable.card_bg_unlocked)
        h.itemView.setOnClickListener { onTap(app, !locked) }
    }
}
