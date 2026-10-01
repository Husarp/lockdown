package com.husarp.lockdown

import android.app.Application
import com.husarp.lockdown.data.ModesStore
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.data.UsageStore
import com.husarp.lockdown.remind.Reminders

class LockdownApp : Application() {
    override fun onCreate() {
        super.onCreate()
        Store.init(this)
        UsageStore.init(this)
        ModesStore.init(this)
        Reminders.schedule(this)
    }
}
