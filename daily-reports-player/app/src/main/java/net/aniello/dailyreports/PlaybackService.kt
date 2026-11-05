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

        // Two-second breather between reports (Luddy, 2026-11-05):
        // when one item ends and the player auto-advances, hold the
        // next item paused for two seconds before it starts. Manual
        // skips are not delayed, and a user pressing play during the
        // breather starts immediately.
        val gapHandler = android.os.Handler(android.os.Looper.getMainLooper())
        var gapPending: Runnable? = null
        var gapActive = false
        fun cancelGap() {
            gapPending?.let { gapHandler.removeCallbacks(it) }
            gapPending = null
            gapActive = false
        }
        player.addListener(object : androidx.media3.common.Player.Listener {
            override fun onMediaItemTransition(
                mediaItem: androidx.media3.common.MediaItem?,
                reason: Int
            ) {
                if (reason == androidx.media3.common.Player.MEDIA_ITEM_TRANSITION_REASON_AUTO) {
                    gapActive = true
                    player.pause()
                    val start = Runnable {
                        gapPending = null
                        if (gapActive && !player.playWhenReady &&
                            player.currentPosition < 500
                        ) {
                            gapActive = false
                            player.play()
                        } else {
                            gapActive = false
                        }
                    }
                    gapPending = start
                    gapHandler.postDelayed(start, 2000)
                } else if (reason ==
                    androidx.media3.common.Player.MEDIA_ITEM_TRANSITION_REASON_SEEK
                ) {
                    // A manual skip during the breather means "play it
                    // now" — cancel the hold and resume.
                    val wasGap = gapActive
                    cancelGap()
                    if (wasGap && !player.playWhenReady) player.play()
                }
            }

            override fun onPlayWhenReadyChanged(playWhenReady: Boolean, reason: Int) {
                // The listener fires synchronously inside our own
                // pause() call, with gapActive already true — that one
                // keeps the breather. Any play request cancels it.
                if (playWhenReady) cancelGap()
            }
        })

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
