package com.parentalcontrol.app

/** Holds the single live AgentClient so MainActivity and ScreenViewActivity can share one connection. */
object AgentSession {
    var client: AgentClient? = null
}
