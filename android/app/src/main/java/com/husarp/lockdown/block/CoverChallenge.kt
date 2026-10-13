package com.husarp.lockdown.block

import android.content.Context
import android.view.KeyEvent
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.ComposeView
import androidx.compose.ui.platform.compositionContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleRegistry
import androidx.lifecycle.ViewModelStore
import androidx.lifecycle.ViewModelStoreOwner
import androidx.lifecycle.setViewTreeLifecycleOwner
import androidx.lifecycle.setViewTreeViewModelStoreOwner
import androidx.savedstate.SavedStateRegistry
import androidx.savedstate.SavedStateRegistryController
import androidx.savedstate.SavedStateRegistryOwner
import androidx.savedstate.setViewTreeSavedStateRegistryOwner
import com.husarp.lockdown.engine.ChallengeRun
import com.husarp.lockdown.guard.ChallengeRunPanel
import com.husarp.lockdown.ui.theme.LockdownTheme

/**
 * What a Compose view needs from the window it sits in, when no activity is around to give it: a lifecycle, a saved-state
 * registry and a view-model store. The bedtime screen is a window of the accessibility service, so [CoverChallenge]
 * brings its own, and puts them on the window's top view.
 */
internal class CoverOwner : SavedStateRegistryOwner, ViewModelStoreOwner {
    private val registry = LifecycleRegistry(this)
    private val saved = SavedStateRegistryController.create(this)
    override val lifecycle: Lifecycle get() = registry
    override val savedStateRegistry: SavedStateRegistry get() = saved.savedStateRegistry
    override val viewModelStore = ViewModelStore()

    init {
        // the saved-state controller wants both calls while the lifecycle is still new
        saved.performAttach()
        saved.performRestore(null)
        registry.currentState = Lifecycle.State.RESUMED
    }

    fun destroy() {
        registry.currentState = Lifecycle.State.DESTROYED
        viewModelStore.clear()
    }
}

/**
 * The bedtime screen's "Challenge before Dismiss", drawn inside the cover (nothing else can sit over it): C6's
 * [ChallengeRunPanel] in a ComposeView that fills the window and takes the place of the bedtime screen's column while it
 * is up. The cover keeps the answer in [run]; Cancel / Back drops it ([close], and the next Dismiss starts a new run).
 * [key]: the reminder this belongs to ("sleep"). [onPassed] / [onCancel] come from the panel.
 */
internal class CoverChallenge(ctx: Context, val key: String, private val run: ChallengeRun, private val title: String,
                              private val onPassed: () -> Unit, private val onCancel: () -> Unit) {
    private val owner = CoverOwner()
    private val compose = ComposeView(ctx)
    // (the system Back key is Cancel here, as the window takes the keys while the challenge is up)
    private val host = object : FrameLayout(ctx) {
        override fun dispatchKeyEvent(event: KeyEvent): Boolean {
            if (event.keyCode != KeyEvent.KEYCODE_BACK) return super.dispatchKeyEvent(event)
            if (event.action == KeyEvent.ACTION_UP) onCancel()
            return true
        }
    }.apply {
        isFocusableInTouchMode = true
        addView(compose, FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT))
    }
    private var root: View? = null
    private var bedtime: View? = null

    /** Shows the challenge in [root] (the cover window's top view) in place of its [bedtime] content. The owners go on
     *  [root] itself: Compose looks for them from the top of the window down. */
    fun show(root: ViewGroup, bedtime: View) {
        this.root = root; this.bedtime = bedtime
        root.setViewTreeLifecycleOwner(owner)
        root.setViewTreeSavedStateRegistryOwner(owner)
        root.setViewTreeViewModelStoreOwner(owner)
        compose.setContent { CoverChallengeContent(run, title, onPassed, onCancel) }
        bedtime.visibility = View.GONE
        root.addView(host, FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT))
        host.requestFocus()
    }

    /** Takes the challenge away and gives the bedtime screen back (the run is dropped with this object). */
    fun close() {
        compose.disposeComposition()
        (host.parent as? ViewGroup)?.removeView(host)
        bedtime?.visibility = View.VISIBLE
        owner.destroy()
        // the window's Compose recomposer went with the lifecycle: the next challenge in this window makes its own
        root?.apply {
            compositionContext = null
            setViewTreeLifecycleOwner(null); setViewTreeSavedStateRegistryOwner(null); setViewTreeViewModelStoreOwner(null)
        }
        root = null; bedtime = null
    }
}

/** The panel in the cover's dark look, centred and no wider than the cover's column, scrolling when it doesn't fit
 *  (with the keyboard up, or a small screen); the keyboard's height is left free. */
@Composable
private fun CoverChallengeContent(run: ChallengeRun, title: String, onPassed: () -> Unit, onCancel: () -> Unit) {
    LockdownTheme(pref = "dark") {
        val cs = MaterialTheme.colorScheme
        Box(Modifier.fillMaxSize().imePadding().padding(horizontal = 16.dp), contentAlignment = Alignment.Center) {
            Column(Modifier.widthIn(max = 440.dp).fillMaxWidth().verticalScroll(rememberScrollState()).padding(vertical = 24.dp)) {
                Surface(color = cs.surfaceContainerLow, contentColor = cs.onSurface, shape = RoundedCornerShape(20.dp)) {
                    ChallengeRunPanel(run, onPassed, onCancel, Modifier.fillMaxWidth().padding(16.dp), title = title)
                }
            }
        }
    }
}
