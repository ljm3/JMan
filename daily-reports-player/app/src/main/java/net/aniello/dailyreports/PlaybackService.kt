package net.aniello.dailyreports

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.media.AudioManager
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.datasource.DefaultDataSource
import androidx.media3.datasource.DefaultHttpDataSource
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.media3.session.MediaSession
import androidx.media3.session.MediaSessionService

/**
 * Foreground media playback service. Playback runs here, not in the
 * activity, so it survives backgrounding, drives the media notification,
 * and answers Bluetooth / car media controls through the media session.
 *
 * Audio focus is handled by ExoPlayer (setAudioAttributes with
 * handleAudioFocus = true): a phone call pauses playback and it resumes
 * when focus returns. Becoming-noisy (headphones unplugged) pauses too.
 */
class PlaybackService : MediaSessionService() {

    private var mediaSession: MediaSession? = null
    private var noisyReceiver: BroadcastReceiver? = null

    override fun onCreate() {
        Diag.install(this)
        super.onCreate()
        try {
            onCreateInner()
            Diag.phase(this, "playback service created")
        } catch (e: Exception) {
            Diag.record(this, "PlaybackService.onCreate", e)
            throw e
        }
    }

    private fun onCreateInner() {
        val auth = Credentials.authHeader(this)
        val httpFactory = DefaultHttpDataSource.Factory()
            .setUserAgent("DailyReportsPlayer/" + BuildConfig.VERSION_NAME)
            .setAllowCrossProtocolRedirects(true)
        if (auth != null) {
            httpFactory.setDefaultRequestProperties(mapOf("Authorization" to auth))
        }
        val dataSourceFactory = DefaultDataSource.Factory(this, httpFactory)
        val player = ExoPlayer.Builder(this)
            .setMediaSourceFactory(DefaultMediaSourceFactory(dataSourceFactory))
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(C.USAGE_MEDIA)
                    .setContentType(C.AUDIO_CONTENT_TYPE_SPEECH)
                    .build(),
                /* handleAudioFocus = */ true
            )
            .setWakeMode(C.WAKE_MODE_NETWORK)
            .build()

        noisyReceiver = object : BroadcastReceiver() {
            override fun onReceive(context: Context?, intent: Intent?) {
                if (intent?.action == AudioManager.ACTION_AUDIO_BECOMING_NOISY) {
                    player.pause()
                }
            }
        }
        // Flag-safe registration: targetSdk 34 requires an explicit
        // export flag for context-registered receivers.
        androidx.core.content.ContextCompat.registerReceiver(
            this, noisyReceiver,
            IntentFilter(AudioManager.ACTION_AUDIO_BECOMING_NOISY),
            androidx.core.content.ContextCompat.RECEIVER_EXPORTED
        )

        // No app-side pause between reports (Luddy, 2026-11-06):
        // v1.23 added a two-second hold on auto-advance, but every
        // report file now ends with two seconds of silence baked
        // in, so the app pause only stacked the gap to ~4 seconds.
        // Removed at his direction — the file's own tail is the
        // breather; auto-advance plays straight through.

        val openApp = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        mediaSession = MediaSession.Builder(this, player)
            .setSessionActivity(openApp)
            .build()
    }

    override fun onGetSession(controllerInfo: MediaSession.ControllerInfo): MediaSession? =
        mediaSession

    override fun onDestroy() {
        noisyReceiver?.let {
            try { unregisterReceiver(it) } catch (e: Exception) { /* already gone */ }
        }
        noisyReceiver = null
        mediaSession?.run {
            player.release()
            release()
        }
        mediaSession = null
        super.onDestroy()
    }
}
