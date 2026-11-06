package com.parentalcontrol.app

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress

/** Finds the PC on the local Wi-Fi network by UDP broadcast, so the parent only needs the PIN. */
object Discovery {
    private const val PORT = 355
    private val PING = "PARENTALAGENT_DISCOVER".toByteArray()
    private const val PONG = "PARENTALAGENT_HERE"

    /** Broadcasts on the local subnet and returns the IP of the first PC that answers, or null. */
    suspend fun findAgent(timeoutMs: Int = 2500): String? = withContext(Dispatchers.IO) {
        var socket: DatagramSocket? = null
        try {
            socket = DatagramSocket().apply {
                broadcast = true
                soTimeout = timeoutMs
            }
            val target = InetAddress.getByName("255.255.255.255")
            socket.send(DatagramPacket(PING, PING.size, target, PORT))
            val buf = ByteArray(64)
            val packet = DatagramPacket(buf, buf.size)
            val deadline = System.currentTimeMillis() + timeoutMs
            while (System.currentTimeMillis() < deadline) {
                socket.soTimeout = (deadline - System.currentTimeMillis()).toInt().coerceAtLeast(50)
                socket.receive(packet)
                val text = String(packet.data, 0, packet.length)
                if (text.startsWith(PONG)) return@withContext packet.address.hostAddress
            }
            null
        } catch (e: Exception) {
            null
        } finally {
            socket?.close()
        }
    }
}
