package net.aniello.approve

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.media.AudioManager
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.datasource.DataSpec
import androidx.media3.datasource.DefaultDataSource
import androidx.media3.datasource.DefaultHttpDataSource
import androidx.media3.datasource.ResolvingDataSource
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.media3.session.MediaSession
import androidx.media3.session.MediaSessionService

/**
 * Foreground media playback service, mirrored from the Daily Reports
 * Player (Luddy's standing lessons): playback runs here, not in the
 * activity, so it survives backgrounding, drives the media
 * notification, and answers Bluetooth / car media controls through
 * the media session. Audio focus is handled by ExoPlayer; becoming
 * noisy (headphones unplugged) pauses.
 *
 * One change from the player: the Authorization header is resolved
 * FRESH on every request through a ResolvingDataSource instead of
 * being captured when the service is created, so signing in (or
 * re-signing in) inside the app takes effect for playback immediately
 * — no stale-credential playback failures.
 *
 * The same player renders audio and video: the activity's PlayerView
 * shows video when the entry's media is a video file and the standard
 * audio controller otherwise.
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
        val httpFactory = DefaultHttpDataSource.Factory()
            .setUserAgent("Approve/" + BuildConfig.VERSION_NAME)
            .setAllowCrossProtocolRedirects(true)
        val baseFactory = DefaultDataSource.Factory(this, httpFactory)
        val resolvingFactory = ResolvingDataSource.Factory(baseFactory) { dataSpec: DataSpec ->
            val auth = Credentials.authHeader(this)
            if (auth == null) dataSpec
            else dataSpec.buildUpon()
                .setHttpRequestHeaders(mapOf("Authorization" to auth))
                .build()
        }
        val player = ExoPlayer.Builder(this)
            .setMediaSourceFactory(DefaultMediaSourceFactory(resolvingFactory))
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
