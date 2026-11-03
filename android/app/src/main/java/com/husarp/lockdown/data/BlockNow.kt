package com.husarp.lockdown.data

import com.husarp.lockdown.engine.Emergency
import com.husarp.lockdown.engine.Pause
import com.husarp.lockdown.engine.Rule
import com.husarp.lockdown.engine.RuleType
import java.time.LocalDateTime
import java.time.LocalTime

/** Immediate changes are applied to saved state, never to an editor's other draft fields. */
object BlockNow {
    fun hour(now: LocalDateTime): LocalDateTime = now.plusHours(1)
    fun midnight(now: LocalDateTime): LocalDateTime = now.toLocalDate().plusDays(1).atStartOfDay()
    fun until(now: LocalDateTime, time: LocalTime, tomorrow: Boolean): LocalDateTime =
        now.toLocalDate().plusDays(if (tomorrow) 1 else 0).atTime(time)

    fun end(rules: List<Rule>): LocalDateTime? = rules.filter { it.type == RuleType.TEMPORARY }
        .mapNotNull { runCatching { LocalDateTime.parse(it.tempUntil) }.getOrNull() }.maxOrNull()

    fun merge(rules: List<Rule>, until: LocalDateTime): List<Rule> {
        val end = maxOf(end(rules) ?: until, until)
        return rules.filterNot { it.type == RuleType.TEMPORARY } + Rule(RuleType.TEMPORARY, tempUntil = end.toString())
    }

    fun apply(c: Config, id: String, group: Boolean, until: LocalDateTime, now: LocalDateTime): Config {
        require(until.isAfter(now)) { "Choose a future time" }
        return if (group) c.copy(groups = c.groups.map { if (it.id == id) it.copy(rules = merge(it.rules, until)) else it })
        else c.copy(items = c.items.map { if (it.id == id) it.copy(rules = merge(it.rules, until)) else it })
    }

    fun notice(c: Config, id: String, group: Boolean, now: LocalDateTime): String {
        if (!c.enabled) return "Blocking is off. This rule starts when blocking is on."
        if (Pause.state(c.pause, now) != null) return "Your blocks are paused. This rule starts when the pause ends."
        val disabled = if (group) c.groups.firstOrNull { it.id == id }?.disabled else c.items.firstOrNull { it.id == id }?.disabled
        if (disabled == true) return "This block is paused. Resume it to enforce this rule."
        val ids = if (group) c.groups.firstOrNull { it.id == id }?.memberIds.orEmpty() else listOf(id)
        if (ids.any { Emergency.itemUntil(c.allUses(), it, now) != null })
            return "An emergency unlock is active. This rule applies when it ends."
        return "Uses the saved blocking settings."
    }
}
