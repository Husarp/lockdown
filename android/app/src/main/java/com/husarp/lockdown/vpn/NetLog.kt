package com.husarp.lockdown.vpn

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

/** A live, in-memory ring of recent DNS lookups the filter saw (newest first), for the Network Log screen. */
object NetLog {
    data class Entry(val time: Long, val host: String, val blocked: Boolean)

    private const val CAP = 500
    private val _entries = MutableStateFlow<List<Entry>>(emptyList())
    val entries: StateFlow<List<Entry>> = _entries

    fun add(host: String, blocked: Boolean) {
        val e = Entry(System.currentTimeMillis(), host, blocked)
        _entries.value = (listOf(e) + _entries.value).take(CAP)
    }

    fun clear() { _entries.value = emptyList() }
}
