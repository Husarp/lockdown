package com.husarp.lockdown.data

import java.io.File
import java.util.UUID

/** Stage every file before replacing any; keep originals until the whole operation succeeds. */
internal object FileWrites {
    val lock = Any()

    fun replace(updates: List<Pair<File, String>>, move: (File, File) -> Boolean = { a, b -> a.renameTo(b) }) {
        val staged = updates.map { (file, _) -> File(file.path + ".tmp") }
        val originals = mutableMapOf<File, File?>()
        val committed = mutableListOf<File>()
        var complete = false
        try {
            updates.forEachIndexed { i, (file, text) ->
                check(!file.exists() || file.isFile) { "${file.name} is not a file" }
                staged[i].writeText(text)
                originals[file] = if (file.exists()) File(file.path + ".rollback-" + UUID.randomUUID()).also {
                    file.copyTo(it) // any backup failure happens before the first replacement
                } else null
            }
            updates.forEachIndexed { i, (file, _) ->
                check(move(staged[i], file)) { "Could not replace ${file.name}" }
                committed.add(file)
            }
            complete = true
        } catch (failure: Exception) {
            var restored = true
            for (file in committed.asReversed()) {
                val original = originals[file]
                val ok = if (original == null) file.delete() else original.renameTo(file)
                if (!ok) {
                    restored = false
                    failure.addSuppressed(IllegalStateException("Could not restore ${file.name}; original kept at $original"))
                }
            }
            if (restored) originals.values.filterNotNull().forEach { it.delete() }
            throw failure
        } finally {
            staged.forEach { if (it.isFile) it.delete() }
            if (complete) originals.values.filterNotNull().forEach { it.delete() }
        }
    }
}
