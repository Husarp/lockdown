package com.husarp.lockdown.block

import com.husarp.lockdown.data.Config
import com.husarp.lockdown.engine.Keywords as Engine

object Keywords {
    /** The first blocked keyword in [text] (a browser address / search box), or null. Uses the full engine. */
    fun hit(cfg: Config, text: String?): String? {
        if (!cfg.enabled || text.isNullOrBlank()) return null
        return Engine.find(text, text, cfg.keywords)   // pass as both address and title: matches a URL or a search phrase
    }
}
