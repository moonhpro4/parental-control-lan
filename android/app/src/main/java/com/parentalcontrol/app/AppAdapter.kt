package com.parentalcontrol.app

import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView

class AppAdapter(private val onTap: (AppInfo, Boolean) -> Unit) :
    RecyclerView.Adapter<AppAdapter.VH>() {

    private var items = listOf<AppInfo>()
    private var blocked = setOf<String>()

    fun submit(apps: List<AppInfo>, blockedSet: Set<String>) {
        items = apps
        blocked = blockedSet
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
        val locked = blocked.contains(app.exe.lowercase())
        h.name.text = app.name
        h.exe.text = app.exe
        h.state.text = if (locked) "LOCKED" else "Unlocked"
        h.state.setTextColor(if (locked) Color.parseColor("#C62828") else Color.parseColor("#2E7D32"))
        h.itemView.background = GradientDrawable().apply {
            cornerRadius = 24f
            setColor(if (locked) Color.parseColor("#FDE0E0") else Color.parseColor("#E3F5E6"))
            setStroke(3, if (locked) Color.parseColor("#E57373") else Color.parseColor("#81C784"))
        }
        h.itemView.setOnClickListener { onTap(app, !locked) }
    }
}
