package net.aniello.mediamanager

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import java.io.File

/**
 * Second net for restarting the app after a self-update (Luddy,
 * 2026-11-05). Android delivers ACTION_MY_PACKAGE_REPLACED to the
 * freshly updated app; when the update flow set the marker file
 * first, this receiver consumes it and relaunches MainActivity.
 * (The install session's own relaunch intent is the first net; if
 * the platform blocks a background activity start here, the app
 * simply stays closed, as before.)
 */
class UpdateRestartReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_MY_PACKAGE_REPLACED) return
        val marker = File(context.filesDir, "relaunch-after-update")
        if (!marker.exists()) return
        marker.delete()
        val launch = Intent(context, MainActivity::class.java).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        }
        try {
            context.startActivity(launch)
        } catch (e: Exception) {
            // Background-start blocked on this build; nothing to do.
        }
    }
}
