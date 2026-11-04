package com.parentalcontrol.app

import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

data class AppInfo(val name: String, val exe: String)

class AgentClient(private val listener: Listener) {

    interface Listener {
        fun onAuthResult(ok: Boolean)
        fun onApps(apps: List<AppInfo>, blocked: Set<String>)
        fun onBlocked(blocked: Set<String>)
        fun onLockResult(ok: Boolean)
        fun onLockState(locked: Boolean)
        fun onUnlockResult(ok: Boolean)
        fun onUninstalled()
        fun onDisconnected(reason: String)
    }

    private val http = OkHttpClient.Builder()
        .connectTimeout(4, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .pingInterval(10, TimeUnit.SECONDS)
        .build()

    @Volatile private var ws: WebSocket? = null
    @Volatile private var pin = ""

    fun connect(ip: String, pin: String) {
        close()
        this.pin = pin
        val req = Request.Builder().url("ws://$ip:354").build()
        ws = http.newWebSocket(req, object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                if (webSocket !== ws) return
                handle(webSocket, text)
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                webSocket.close(1000, null)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                if (webSocket !== ws) return
                ws = null
                listener.onDisconnected(reason.ifEmpty { "closed" })
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                if (webSocket !== ws) return
                ws = null
                listener.onDisconnected(t.message ?: "connection failed")
            }
        })
    }

    private fun handle(webSocket: WebSocket, text: String) {
        val o = try { JSONObject(text) } catch (e: Exception) { return }
        when (o.optString("type")) {
            "hello" -> {
                val mac = Mac.getInstance("HmacSHA256")
                mac.init(SecretKeySpec(pin.toByteArray(), "HmacSHA256"))
                val digest = mac.doFinal(o.getString("nonce").toByteArray())
                    .joinToString("") { "%02x".format(it) }
                webSocket.send(JSONObject().put("cmd", "auth").put("hmac", digest).toString())
            }
            "auth" -> listener.onAuthResult(o.optBoolean("ok"))
            "apps" -> {
                val arr = o.getJSONArray("apps")
                val apps = (0 until arr.length()).map {
                    val a = arr.getJSONObject(it)
                    AppInfo(a.getString("name"), a.getString("exe"))
                }
                listener.onApps(apps, parseBlocked(o))
            }
            "blocked" -> listener.onBlocked(parseBlocked(o))
            "lock" -> listener.onLockResult(o.optBoolean("ok"))
            "uninstall" -> listener.onUninstalled()
            "lock_state" -> listener.onLockState(o.optBoolean("locked"))
            "unlock" -> listener.onUnlockResult(o.optBoolean("ok"))
        }
    }

    private fun parseBlocked(o: JSONObject): Set<String> {
        val arr = o.optJSONArray("blocked") ?: return emptySet()
        return (0 until arr.length()).map { arr.getString(it).lowercase() }.toSet()
    }

    private fun send(o: JSONObject): Boolean = ws?.send(o.toString()) ?: false

    fun lock() = send(JSONObject().put("cmd", "lock").put("lang", java.util.Locale.getDefault().language))
    fun unlock() = send(JSONObject().put("cmd", "unlock"))
    fun uninstall() = send(JSONObject().put("cmd", "uninstall"))
    fun listApps() = send(JSONObject().put("cmd", "list_apps"))
    fun setBlocked(exe: String, blocked: Boolean) =
        send(JSONObject().put("cmd", "set_block").put("exe", exe).put("blocked", blocked))

    fun close() {
        val s = ws
        ws = null
        s?.cancel()
    }
}
