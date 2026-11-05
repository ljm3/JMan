package net.aniello.dailyreports

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.ClipData
import android.content.ClipboardManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.res.ColorStateList
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.StateListDrawable
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.SeekBar
import android.widget.TextView
import android.widget.Toast
import androidx.core.content.FileProvider
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.Player
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.text.SimpleDateFormat
import java.util.Locale
import java.util.concurrent.Executors
import kotlin.concurrent.thread

/**
 * Daily Reports Player — plays the day's report audio (summaries first,
 * then full texts) from the same manifest that drives ljm3.me/daily.
 */
class MainActivity : Activity() {

    companion object {
        private const val MANIFEST_URL = "https://ljm3.me/daily/manifest.json"
        private const val AUDIO_BASE = "https://ljm3.me/daily/audio/"
        private const val UPDATE_URL = "https://software.aniello.net/apps/daily-reports-player-version.json"
        private const val APK_BASE = "https://software.aniello.net/apps/"

        // Canonical schedule order and display names, mirroring REPORTS in
        // build_daily.py (the Daily page builder) exactly.
        private val REPORTS = listOf(
            "news-summary" to "News Summary",
            "legislature" to "Legislature Report",
            "ai-news" to "AI News Summary",
            "dc-news" to "Data Center News Summary",
            "banking" to "Banking News Summary",
            "general-science" to "General Science News Summary",
            "meta-muse" to "Meta Muse News Summary",
            "ai-law-news" to "AI Law News (Weekly)",
            "dc-law-news" to "Data Center Law News (Weekly)",
            "nightly-ops" to "Operations and Health Report"
        )

        private val BG = Color.parseColor("#0b0f16")
        private val PANEL = Color.parseColor("#121926")
        private val BORDER = Color.parseColor("#263449")
        private val TEXT = Color.parseColor("#e8edf2")
        private val SUBTEXT = Color.parseColor("#9fb3c8")
        private val GOLD = Color.parseColor("#c9a86a")
        private val BLUE = Color.parseColor("#1f6feb")
    }

    private data class Track(
        val prefix: String,
        val name: String,
        val kind: String, // "Summary" or "Full Text"
        val secs: Double,
        val url: String
    ) {
        val label: String get() = "$name — $kind"
    }

    private val bgExecutor = Executors.newSingleThreadExecutor()
    private val ui = Handler(Looper.getMainLooper())

    private var controller: MediaController? = null
    private var manifest: JSONObject? = null
    private var dates: List<String> = emptyList()
    private var dateIdx = 0
    private var tracks: List<Track> = emptyList()
    private var playlistLoadedFor: String? = null

    // Views
    private lateinit var root: LinearLayout
    private lateinit var signInBox: LinearLayout
    private lateinit var mainBox: LinearLayout
    private lateinit var rowsBox: LinearLayout
    private lateinit var dateLabel: TextView
    private lateinit var nowTitle: TextView
    private lateinit var nowTime: TextView
    private lateinit var seekBar: SeekBar
    private lateinit var playBtn: Button
    private lateinit var statusLine: TextView
    private var seeking = false
    private var lastManifestFetchMs = 0L
    private var lastUpdateCheckMs = 0L
    private var lastPromptedVersionCode = 0

    private var tickCount = 0

    private val ticker = object : Runnable {
        override fun run() {
            refreshProgress()
            // Last-play point (Luddy, 2026-11-05): while playing,
            // save where we are every 5 seconds, so if the player is
            // killed in the background it resumes from here instead
            // of starting the day over.
            tickCount++
            if (tickCount % 10 == 0 && controller?.isPlaying == true) savePlayPoint()
            ui.postDelayed(this, 500)
        }
    }

    private val playerListener = object : Player.Listener {
        override fun onIsPlayingChanged(isPlaying: Boolean) {
            refreshPlayButton()
            if (!isPlaying) savePlayPoint()
        }
        override fun onMediaItemTransition(mediaItem: MediaItem?, reason: Int) {
            refreshNowPlaying()
            highlightCurrentRow()
            savePlayPoint()
        }
        override fun onPlaybackStateChanged(state: Int) {
            refreshPlayButton()
            refreshNowPlaying()
            if (state == Player.STATE_ENDED) {
                // The day played through: clear its saved point so the
                // next visit starts at the beginning, as it should.
                val date = playlistLoadedFor ?: return
                getSharedPreferences("playstate", Context.MODE_PRIVATE).edit()
                    .remove("mid:$date").remove("pos:$date").apply()
            }
        }
    }

    override fun onPause() {
        savePlayPoint()
        super.onPause()
    }

    /**
     * The last-play point (Luddy, 2026-11-05): the current track's id
     * and position, saved per day. The manifest keeps three days, so
     * each day remembers its own place. Saved while playing (every 5
     * seconds), on pause, and on every track change — cheap insurance
     * against the process being killed while the screen is dark.
     */
    private fun savePlayPoint() {
        try {
            val c = controller ?: return
            val date = playlistLoadedFor ?: return
            val item = c.currentMediaItem ?: return
            getSharedPreferences("playstate", Context.MODE_PRIVATE).edit()
                .putString("mid:$date", item.mediaId)
                .putLong("pos:$date", c.currentPosition)
                .apply()
        } catch (e: Exception) { /* a failed save never disturbs playback */ }
    }

    /** The saved point for a day, resolved against the day's items. */
    private fun savedPoint(date: String, items: List<MediaItem>): Pair<Int, Long>? {
        val prefs = getSharedPreferences("playstate", Context.MODE_PRIVATE)
        val mid = prefs.getString("mid:$date", null) ?: return null
        val pos = prefs.getLong("pos:$date", 0L)
        var idx = items.indexOfFirst { it.mediaId == mid }
        if (idx < 0) return null
        var p = pos
        val durMs = (tracks.getOrNull(idx)?.secs ?: 0.0) * 1000
        if (durMs > 0 && p >= durMs - 3000) {
            // That track was effectively finished — resume at the next.
            if (idx + 1 < items.size) { idx += 1; p = 0L } else { idx = 0; p = 0L }
        }
        Diag.phase(this, "play point restored for $date: item $idx at ${p}ms")
        return idx to p
    }

    // ------------------------------------------------------------------ UI

    override fun onCreate(savedInstanceState: Bundle?) {
        Diag.install(this)
        super.onCreate(savedInstanceState)
        try {
            requestNotificationPermission()
            buildUi()
            Diag.phase(this, "ui built")
            showLastCrashIfAny()
            // Last-resort guard: a failure reading stored state must land on the
            // sign-in screen, never close the app (v1.0 launch-crash lesson).
            val signedIn = try { Credentials.hasCredentials(this) } catch (e: Exception) { false }
            Diag.phase(this, "creds checked: signedIn=$signedIn")
            if (signedIn) {
                showMain()
                // Isolated: a playback-connection failure must never tear
                // down startup or bounce back to sign-in (v1.2 login loop:
                // the SessionToken throw landed in the onCreate catch,
                // which re-showed the sign-in screen on every launch).
                try { connectController() } catch (e: Exception) {
                    Diag.record(this, "connectController(start)", e)
                    statusLine.text = "Playback unavailable: ${e.javaClass.simpleName}"
                }
                loadManifest()
            } else {
                showSignIn()
            }
            checkForUpdate()
            Diag.phase(this, "startup complete")
        } catch (e: Exception) {
            Diag.record(this, "MainActivity.onCreate", e)
            try {
                statusLine.text = "Startup hit a problem: ${e.javaClass.simpleName}: ${e.message}"
                showSignIn()
            } catch (ignored: Exception) { /* UI itself failed; trace is saved */ }
        }
    }

    /** If the previous launch died, show what killed it — on screen, readable. */
    private fun showLastCrashIfAny() {
        val full = Diag.lastCrashFull(this) ?: return
        val scroll = ScrollView(this)
        val tv = TextView(this)
        tv.text = full
        tv.textSize = 12f
        tv.setPadding(dp(16), dp(8), dp(16), dp(8))
        scroll.addView(tv)
        AlertDialog.Builder(this)
            .setTitle("Last launch ran into a problem")
            .setView(scroll)
            .setPositiveButton("Copy") { _, _ ->
                val cm = getSystemService(android.content.ClipboardManager::class.java)
                cm?.setPrimaryClip(android.content.ClipData.newPlainText("Daily Reports Player error", full))
                toast("Copied — paste it to JMan.")
            }
            .setNeutralButton("Clear") { _, _ -> Diag.clearCrash(this) }
            .setNegativeButton("Close", null)
            .show()
    }

    override fun onDestroy() {
        ui.removeCallbacks(ticker)
        controller?.removeListener(playerListener)
        controller?.release()
        controller = null
        super.onDestroy()
    }

    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).toInt()

    private fun panelBox(): LinearLayout {
        val box = LinearLayout(this)
        box.orientation = LinearLayout.VERTICAL
        val bg = android.graphics.drawable.GradientDrawable()
        bg.setColor(PANEL)
        bg.setStroke(dp(1), BORDER)
        bg.cornerRadius = dp(12).toFloat()
        box.background = bg
        box.setPadding(dp(14), dp(12), dp(14), dp(12))
        return box
    }

    private fun label(text: String, size: Float, color: Int, bold: Boolean = false): TextView {
        val t = TextView(this)
        t.text = text
        t.textSize = size
        t.setTextColor(color)
        if (bold) t.typeface = Typeface.DEFAULT_BOLD
        return t
    }

    private fun actionButton(text: String, primary: Boolean = false): Button {
        val b = Button(this)
        b.text = text
        b.textSize = 15f
        b.setTextColor(Color.WHITE)
        b.setBackgroundColor(if (primary) BLUE else Color.parseColor("#1b2637"))
        b.isAllCaps = false
        return b
    }

    /**
     * Pill-shaped button with a border (Luddy, 2026-11-05): fully rounded,
     * thin border in the normal state; the highlight shows only at the
     * tap — while pressed, the border thickens and turns gold, the fill
     * warms, and the text turns gold. Nothing stays highlighted.
     */
    private fun pillButton(text: String): Button {
        fun shape(fill: Int, strokeColor: Int, strokeWidth: Int): android.graphics.drawable.GradientDrawable {
            val g = android.graphics.drawable.GradientDrawable()
            g.shape = android.graphics.drawable.GradientDrawable.RECTANGLE
            g.cornerRadius = dp(999).toFloat()
            g.setColor(fill)
            g.setStroke(dp(strokeWidth), strokeColor)
            return g
        }
        val bg = StateListDrawable()
        bg.addState(intArrayOf(android.R.attr.state_pressed), shape(Color.parseColor("#2a2118"), GOLD, 2))
        bg.addState(intArrayOf(), shape(PANEL, BORDER, 1))
        val b = Button(this)
        b.text = text
        b.textSize = 14f
        b.isAllCaps = false
        b.background = bg
        b.setTextColor(
            ColorStateList(
                arrayOf(
                    intArrayOf(android.R.attr.state_pressed),
                    intArrayOf()
                ),
                intArrayOf(GOLD, TEXT)
            )
        )
        b.setPadding(dp(16), dp(7), dp(16), dp(7))
        b.minWidth = 0
        b.minimumWidth = 0
        return b
    }

    /**
     * Player transport button (Luddy, 2026-11-05): a real button with
     * distinct edges — rounded corners and a visible border — instead
     * of the flat action-button fill.
     */
    private fun controlButton(text: String, primary: Boolean = false): Button {
        val b = Button(this)
        b.text = text
        // About 10% smaller per button (Luddy, 2026-11-05): text
        // 15sp -> 13.5sp and the Material 48dp minimum height -> 43dp.
        b.textSize = 13.5f
        b.minHeight = dp(43)
        b.minimumHeight = dp(43)
        b.isAllCaps = false
        b.setTextColor(Color.WHITE)
        val bg = android.graphics.drawable.GradientDrawable()
        bg.setColor(if (primary) BLUE else Color.parseColor("#1b2637"))
        bg.setStroke(dp(1), if (primary) Color.parseColor("#6ea8ff") else BORDER)
        bg.cornerRadius = dp(10).toFloat()
        b.background = bg
        b.minWidth = 0
        b.minimumWidth = 0
        return b
    }

    private fun buildUi() {
        root = LinearLayout(this)
        root.orientation = LinearLayout.VERTICAL
        root.setBackgroundColor(BG)
        root.setPadding(dp(14), dp(14), dp(14), dp(10))

        // Header: the AE logo at the left; the title + version form a
        // tight two-line stack in their own full-height column with
        // center gravity, so the pair's center sits exactly on the
        // logo's center (Luddy, 2026-11-05). The Settings pill rides
        // at the right end of the header row, clear of the stack —
        // in 1.11 the pill shared the title's row, set that row's
        // height, and the text barely moved.
        val headerRow = LinearLayout(this)
        headerRow.orientation = LinearLayout.HORIZONTAL
        headerRow.gravity = Gravity.CENTER_VERTICAL
        val logo = ImageView(this)
        logo.setImageResource(R.drawable.ae_logo)
        logo.adjustViewBounds = true
        logo.layoutParams = LinearLayout.LayoutParams(dp(56), dp(56))
        headerRow.addView(logo)
        val headerCol = LinearLayout(this)
        headerCol.orientation = LinearLayout.VERTICAL
        headerCol.gravity = Gravity.CENTER_VERTICAL
        val hclp = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, 1f)
        hclp.leftMargin = dp(10)
        headerCol.layoutParams = hclp
        headerRow.addView(headerCol)

        val title = label("Daily Reports Player", 20f, GOLD, bold = true)
        title.includeFontPadding = false
        headerCol.addView(title)

        // Installed version, shown small right under the app title
        // (Luddy, 2026-11-05). Read from the package so it can never
        // drift from the build actually on the phone.
        val versionText = try {
            "Version:  " + (packageManager.getPackageInfo(packageName, 0).versionName ?: "")
        } catch (e: Exception) { "" }
        if (versionText.isNotBlank()) {
            val versionLine = label(versionText, 12f, SUBTEXT)
            versionLine.includeFontPadding = false
            versionLine.setPadding(0, 0, 0, 0)
            val vlp = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT
            )
            vlp.topMargin = dp(1)
            versionLine.layoutParams = vlp
            headerCol.addView(versionLine)
        }
        val settingsBtn = pillButton("Settings")
        settingsBtn.setOnClickListener { showSettings() }
        headerRow.addView(settingsBtn)
        root.addView(headerRow)

        statusLine = label("", 13f, SUBTEXT)
        statusLine.setPadding(0, dp(6), 0, dp(6))
        root.addView(statusLine)

        buildSignInBox()
        root.addView(signInBox)
        buildMainBox()
        root.addView(mainBox)

        setContentView(root)
    }

    private fun buildSignInBox() {
        signInBox = panelBox()
        signInBox.layoutParams = LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
        )
        signInBox.addView(label("Sign in with your dashboard credentials.", 15f, TEXT))
        val user = EditText(this)
        user.hint = "Username"
        user.setText(Credentials.username(this) ?: "luddy")
        user.setTextColor(TEXT)
        user.setHintTextColor(SUBTEXT)
        user.inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
        val pass = EditText(this)
        pass.hint = "Password"
        pass.setTextColor(TEXT)
        pass.setHintTextColor(SUBTEXT)
        pass.inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
        signInBox.addView(user)
        signInBox.addView(pass)
        val btn = actionButton("Sign In", primary = true)
        btn.setOnClickListener {
            val u = user.text.toString().trim()
            val p = pass.text.toString()
            if (u.isEmpty() || p.isEmpty()) {
                toast("Enter both a username and a password.")
                return@setOnClickListener
            }
            Credentials.save(this, u, p)
            statusLine.text = "Signing in…"
            // Verify the credentials against the manifest before entering.
            bgExecutor.execute {
                val ok = fetchManifestJson() != null
                ui.post {
                    if (ok) {
                        statusLine.text = ""
                        showMain()
                        restartPlaybackService()
                        connectController()
                        loadManifest()
                    } else {
                        Credentials.clear(this)
                        statusLine.text = "Sign-in failed — check the username and password."
                    }
                }
            }
        }
        signInBox.addView(btn)
    }

    private fun buildMainBox() {
        mainBox = LinearLayout(this)
        mainBox.orientation = LinearLayout.VERTICAL
        mainBox.layoutParams = LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f
        )

        // Date controls
        val dateRow = LinearLayout(this)
        dateRow.orientation = LinearLayout.HORIZONTAL
        dateRow.gravity = Gravity.CENTER_VERTICAL
        // Previous / Next pills, identical size as each other (Luddy,
        // 2026-11-05); the highlight shows only at the tap.
        val prevDay = pillButton("Previous")
        val nextDay = pillButton("Next")
        prevDay.layoutParams = LinearLayout.LayoutParams(dp(104), ViewGroup.LayoutParams.WRAP_CONTENT)
        nextDay.layoutParams = LinearLayout.LayoutParams(dp(104), ViewGroup.LayoutParams.WRAP_CONTENT)
        dateLabel = label("", 16f, TEXT, bold = true)
        dateLabel.gravity = Gravity.CENTER
        dateLabel.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        prevDay.setOnClickListener { if (dateIdx < dates.size - 1) { dateIdx++; showDate() } }
        nextDay.setOnClickListener { if (dateIdx > 0) { dateIdx--; showDate() } }
        dateRow.addView(prevDay)
        dateRow.addView(dateLabel)
        dateRow.addView(nextDay)
        mainBox.addView(dateRow)

        // Track rows (scrollable)
        val scroll = ScrollView(this)
        scroll.layoutParams = LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f
        )
        rowsBox = LinearLayout(this)
        rowsBox.orientation = LinearLayout.VERTICAL
        rowsBox.setPadding(0, dp(8), 0, dp(8))
        scroll.addView(rowsBox)
        mainBox.addView(scroll)

        // Now Playing bar — the time sits at the far right of the
        // title line (Luddy, 2026-11-05).
        val now = panelBox()
        val nowRow = LinearLayout(this)
        nowRow.orientation = LinearLayout.HORIZONTAL
        nowRow.gravity = Gravity.CENTER_VERTICAL
        nowTitle = label("Nothing playing", 15f, TEXT, bold = true)
        nowTitle.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        nowRow.addView(nowTitle)
        nowTime = label("0:00 / 0:00", 13f, SUBTEXT)
        nowRow.addView(nowTime)
        now.addView(nowRow)
        seekBar = SeekBar(this)
        seekBar.max = 1000
        seekBar.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(sb: SeekBar?, progress: Int, fromUser: Boolean) {}
            override fun onStartTrackingTouch(sb: SeekBar?) { seeking = true }
            override fun onStopTrackingTouch(sb: SeekBar?) {
                seeking = false
                val c = controller ?: return
                val dur = c.duration
                if (dur > 0) c.seekTo(seekBar.progress.toLong() * dur / 1000)
            }
        })
        // Seek placement (Luddy, 2026-11-05): within the taller
        // panel (about 25% over the original), the seek bar sits
        // midway between the title line and the buttons — equal
        // spacer above and below it. (First pass put it too close
        // to the title; the 26/4 split overshot toward the buttons.)
        val seekSpacerTop = View(this)
        seekSpacerTop.layoutParams = LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, dp(15))
        now.addView(seekSpacerTop)
        now.addView(seekBar)
        val spacer = View(this)
        spacer.layoutParams = LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, dp(15))
        now.addView(spacer)
        val controls = LinearLayout(this)
        controls.orientation = LinearLayout.HORIZONTAL
        controls.gravity = Gravity.CENTER
        val prev = controlButton("⏮")
        val back = controlButton("−15s")
        playBtn = controlButton("▶", primary = true)
        val fwd = controlButton("+15s")
        val next = controlButton("⏭")
        for (b in listOf(prev, back, playBtn, fwd, next)) {
            val blp = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
            blp.leftMargin = dp(3)
            blp.rightMargin = dp(3)
            b.layoutParams = blp
            controls.addView(b)
        }
        prev.setOnClickListener { controller?.seekToPreviousMediaItem() }
        next.setOnClickListener { controller?.seekToNextMediaItem() }
        back.setOnClickListener { controller?.let { it.seekTo(maxOf(0, it.currentPosition - 15_000)) } }
        fwd.setOnClickListener { controller?.let { it.seekTo(it.currentPosition + 15_000) } }
        playBtn.setOnClickListener {
            val c = controller ?: return@setOnClickListener
            if (c.playbackState == Player.STATE_IDLE || c.playbackState == Player.STATE_ENDED) {
                ensurePlaylistLoaded()
                c.prepare()
                c.play()
            } else if (c.isPlaying) c.pause() else c.play()
        }
        now.addView(controls)
        mainBox.addView(now)
    }

    private fun showSignIn() {
        signInBox.visibility = View.VISIBLE
        mainBox.visibility = View.GONE
    }

    private fun showMain() {
        signInBox.visibility = View.GONE
        mainBox.visibility = View.VISIBLE
    }

    // ------------------------------------------------------------- Playback

    private fun connectController() {
        val token = SessionToken(this, ComponentName(this, PlaybackService::class.java))
        val future = MediaController.Builder(this, token).buildAsync()
        future.addListener({
            try {
                controller = future.get()
                controller?.addListener(playerListener)
                ensurePlaylistLoaded()
                refreshPlayButton()
                refreshNowPlaying()
                ui.removeCallbacks(ticker)
                ui.post(ticker)
            } catch (e: Exception) {
                Diag.record(this, "connectController", e)
                statusLine.text = "Playback service unavailable: ${e.javaClass.simpleName}"
            }
        }, mainExecutor())
    }

    private fun mainExecutor() = androidx.core.content.ContextCompat.getMainExecutor(this)

    /** Credentials changed: drop the service so it rebuilds with the new header. */
    private fun restartPlaybackService() {
        controller?.removeListener(playerListener)
        controller?.release()
        controller = null
        playlistLoadedFor = null
        stopService(Intent(this, PlaybackService::class.java))
    }

    private fun buildTracksFor(date: String): List<Track> {
        val entries = manifest?.optJSONObject("entries") ?: return emptyList()
        val out = mutableListOf<Track>()
        val ordered = REPORTS.toMutableList()
        // Any prefix in the manifest but not in the canonical list goes last.
        val keys = entries.keys()
        while (keys.hasNext()) {
            val k = keys.next()
            if (ordered.none { it.first == k }) ordered.add(k to k)
        }
        for (kind in listOf("Summary", "Full Text")) {
            for ((prefix, name) in ordered) {
                val day = entries.optJSONObject(prefix)?.optJSONObject(date) ?: continue
                val secs = if (kind == "Summary") day.optDouble("summary_secs", 0.0)
                           else day.optDouble("full_secs", 0.0)
                if (secs <= 0) continue
                val file = if (kind == "Summary") "$prefix-$date.mp3" else "$prefix-$date-full.mp3"
                out.add(Track(prefix, name, kind, secs, AUDIO_BASE + file))
            }
        }
        return out
    }

    private fun showDate() {
        if (dates.isEmpty()) return
        val date = dates[dateIdx]
        dateLabel.text = prettyDateStacked(date)
        tracks = buildTracksFor(date)
        // playlistLoadedFor is deliberately NOT cleared here (1.21):
        // ensurePlaylistLoaded compares it against the loaded items
        // and leaves a matching list — and its position — untouched.
        renderRows()
        ensurePlaylistLoaded()
    }

    /**
     * The AE eagle-A as session artwork (Luddy, 2026-11-05): Android's
     * media display (lock screen, notification shade) puts the
     * artwork at the left with the title and artist text to its
     * right — the logo beside the text, times left where the system
     * places them. Loaded once from the bundled logo.
     */
    private val logoBytes: ByteArray? by lazy {
        try {
            val bmp = android.graphics.BitmapFactory.decodeResource(resources, R.drawable.ae_logo)
            if (bmp == null) null else {
                val baos = java.io.ByteArrayOutputStream()
                bmp.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, baos)
                baos.toByteArray()
            }
        } catch (e: Exception) { null }
    }

    private fun ensurePlaylistLoaded() {
        val c = controller ?: return
        if (dates.isEmpty()) return
        val date = dates[dateIdx]
        if (playlistLoadedFor == date) return
        val items = tracks.map { t ->
            val meta = MediaMetadata.Builder()
                .setTitle(t.label)
                .setArtist("Daily Reports — ${prettyDate(date)}")
            logoBytes?.let { meta.setArtworkData(it, MediaMetadata.PICTURE_TYPE_FRONT_COVER) }
            MediaItem.Builder()
                .setUri(t.url)
                .setMediaId(t.url)
                .setMediaMetadata(meta.build())
                .build()
        }
        // Exactly this list is already loaded: leave the player
        // completely alone. (Before 1.21 the list was rebuilt on
        // every manifest refresh — and showDate had already cleared
        // playlistLoadedFor, so the keep-position guard below it
        // never fired — which reset playback to the first track,
        // losing Luddy's place whenever he came back to the app.)
        if (playlistLoadedFor == date && c.mediaItemCount == items.size &&
            items.indices.all { c.getMediaItemAt(it).mediaId == items[it].mediaId }) {
            return
        }
        // Restore target: the live position when the current track
        // survives the rebuild; otherwise the day's saved last-play
        // point, which survives the process being killed outright.
        var targetIndex = 0
        var targetPos = 0L
        val wasPlaying = c.isPlaying
        val liveItem = c.currentMediaItem
        val liveIdx = if (liveItem != null) items.indexOfFirst { it.mediaId == liveItem.mediaId } else -1
        if (liveIdx >= 0) {
            targetIndex = liveIdx
            targetPos = c.currentPosition
        } else {
            val p = savedPoint(date, items)
            if (p != null) { targetIndex = p.first; targetPos = p.second }
        }
        c.setMediaItems(items, targetIndex, targetPos)
        c.prepare()
        if (wasPlaying) c.play()
        playlistLoadedFor = date
    }

    private fun renderRows() {
        rowsBox.removeAllViews()
        tracks.forEachIndexed { index, t ->
            val row = LinearLayout(this)
            row.orientation = LinearLayout.HORIZONTAL
            row.gravity = Gravity.CENTER_VERTICAL
            row.setPadding(dp(12), dp(10), dp(12), dp(10))
            val info = LinearLayout(this)
            info.orientation = LinearLayout.VERTICAL
            info.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
            val name = label(t.name, 15f, TEXT, bold = true)
            name.tag = "name"
            info.addView(name)
            info.addView(label(t.kind, 13f, SUBTEXT))
            row.addView(info)
            row.addView(label(fmtTime(t.secs), 14f, GOLD))
            row.setOnClickListener {
                ensurePlaylistLoaded()
                val c = controller ?: return@setOnClickListener
                c.seekTo(index, 0)
                c.prepare()
                c.play()
            }
            row.tag = index
            rowsBox.addView(row)
            val divider = View(this)
            divider.setBackgroundColor(BORDER)
            divider.layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(1))
            rowsBox.addView(divider)
        }
        highlightCurrentRow()
    }

    private fun highlightCurrentRow() {
        val c = controller ?: return
        val current = c.currentMediaItemIndex
        for (i in 0 until rowsBox.childCount) {
            val child = rowsBox.getChildAt(i)
            if (child is LinearLayout && child.tag is Int) {
                val nameTv = child.findViewWithTag<TextView>("name")
                val active = (child.tag as Int) == current && playlistLoadedFor == dates.getOrNull(dateIdx)
                nameTv?.setTextColor(if (active) GOLD else TEXT)
            }
        }
    }

    private fun refreshPlayButton() {
        val c = controller
        playBtn.text = if (c != null && c.isPlaying) "⏸" else "▶"
    }

    private fun refreshNowPlaying() {
        val c = controller ?: return
        val idx = c.currentMediaItemIndex
        val t = tracks.getOrNull(idx)
        nowTitle.text = if (t != null && playlistLoadedFor == dates.getOrNull(dateIdx)) t.label else "Nothing playing"
        refreshProgress()
    }

    private fun refreshProgress() {
        val c = controller ?: return
        if (seeking) return
        val pos = c.currentPosition
        val idx = c.currentMediaItemIndex
        val t = tracks.getOrNull(idx)
        val dur = if (c.duration > 0) c.duration else (t?.secs?.times(1000)?.toLong() ?: 0)
        if (dur > 0) seekBar.progress = (pos * 1000 / dur).toInt() else seekBar.progress = 0
        nowTime.text = "${fmtTime(pos / 1000.0)} / ${fmtTime(dur / 1000.0)}"
    }

    // -------------------------------------------------------------- Manifest

    private fun fetchManifestJson(): JSONObject? {
        val auth = Credentials.authHeader(this) ?: return null
        return try {
            val conn = URL(MANIFEST_URL).openConnection() as HttpURLConnection
            conn.setRequestProperty("Authorization", auth)
            conn.useCaches = false
            conn.setRequestProperty("Cache-Control", "no-cache")
            conn.connectTimeout = 15_000
            conn.readTimeout = 15_000
            if (conn.responseCode != 200) return null
            val text = conn.inputStream.bufferedReader().readText()
            JSONObject(text)
        } catch (e: Exception) {
            null
        }
    }

    private fun loadManifest() {
        lastManifestFetchMs = System.currentTimeMillis()
        if (manifest == null) statusLine.text = "Loading reports…"
        bgExecutor.execute {
            val json = fetchManifestJson()
            ui.post {
                if (json == null) {
                    statusLine.text = "Could not load the report list. Check your connection or credentials (Settings)."
                    return@post
                }
                // Keep the day tab the user is on if it still exists.
                val keepDate = dates.getOrNull(dateIdx)
                manifest = json
                val entries = json.optJSONObject("entries")
                val all = mutableSetOf<String>()
                if (entries != null) {
                    val pk = entries.keys()
                    while (pk.hasNext()) {
                        val days = entries.getJSONObject(pk.next())
                        val dk = days.keys()
                        while (dk.hasNext()) all.add(dk.next())
                    }
                }
                dates = all.sortedDescending()
                val keepIdx = if (keepDate != null) dates.indexOf(keepDate) else -1
                dateIdx = if (keepIdx >= 0) keepIdx else 0
                statusLine.text = ""
                if (dates.isEmpty()) {
                    dateLabel.text = "No reports available"
                } else {
                    showDate()
                }
            }
        }
    }

    /**
     * Refresh the report list whenever the app comes back to the
     * foreground (throttled to once per 30 seconds). Without this, the
     * list was fetched once at first launch and never again while the
     * process lived — new reports published during the morning never
     * appeared until the app was force-stopped (Luddy, 2026-11-05).
     */
    override fun onResume() {
        super.onResume()
        if (manifest != null &&
            System.currentTimeMillis() - lastManifestFetchMs > 30_000) {
            loadManifest()
        }
        // Automatic update check on return to the foreground too —
        // at launch alone, the v1.4 prompt never reached Luddy.
        checkForUpdate()
    }

    // --------------------------------------------------------------- Update

    /**
     * Update check (Luddy, 2026-11-05). Runs automatically at launch and
     * again whenever the app returns to the foreground (throttled), and
     * manually from Settings ("Check for Updates"). Automatic checks
     * prompt at most once per new version per process run, so a "Later"
     * is respected until the app restarts; a manual check always prompts
     * when an update exists and always reports the outcome.
     */
    private fun checkForUpdate(manual: Boolean = false) {
        if (!manual) {
            val now = System.currentTimeMillis()
            if (now - lastUpdateCheckMs < 60_000) return
            lastUpdateCheckMs = now
        }
        val doorErrors = StringBuilder()
        thread {
            try {
                // Two doors to the same version file. The ljm3.me door
                // rides the host the playlist already loads from — on
                // Luddy's phone the software.aniello.net request came
                // back with non-TLS bytes ("Unable to parse TLS packet
                // header", per the v1.6 diagnostics) while ljm3.me
                // worked — so ljm3.me goes first, carrying his saved
                // sign-in (the Daily area sits behind the dashboard
                // login); the direct software.aniello.net file is the
                // fallback door.
                val basic = Credentials.authHeader(this)
                val doors = listOf(
                    "https://ljm3.me/daily/app-version.json" to basic,
                    UPDATE_URL to null)
                var fetched: JSONObject? = null
                var lastError: Exception? = null
                for ((door, auth) in doors) {
                    try {
                        val conn = URL(door).openConnection() as HttpURLConnection
                        conn.useCaches = false
                        conn.setRequestProperty("Cache-Control", "no-cache")
                        conn.setRequestProperty("User-Agent", "DailyReportsPlayer Android")
                        if (auth != null) conn.setRequestProperty("Authorization", auth)
                        conn.connectTimeout = 15_000
                        conn.readTimeout = 15_000
                        if (conn.responseCode != 200) {
                            throw IllegalStateException("The update server answered HTTP ${conn.responseCode}.")
                        }
                        fetched = JSONObject(conn.inputStream.bufferedReader().readText())
                        break
                    } catch (e: Exception) {
                        lastError = e
                        // Record EACH door's failure separately (v1.9):
                        // v1.7/v1.8 kept only the last door's error, which
                        // threw away why the ljm3.me door failed and made
                        // every failure look like the software-host one.
                        Diag.record(this, "checkForUpdate door: $door", e)
                        doorErrors.append(door).append(" — ")
                            .append(e.javaClass.simpleName).append(": ")
                            .append(e.message ?: "no detail").append("\n")
                    }
                }
                val info = fetched ?: throw lastError ?: IllegalStateException("Update check failed.")
                val latest = info.optInt("versionCode", 0)
                val latestName = info.optString("versionName", "")
                val apk = info.optString("apk", "daily-reports-player.apk")
                val notes = info.optString("notes", "")
                val installed = packageManager.getPackageInfo(packageName, 0).longVersionCode
                if (latest > installed) {
                    if (manual || latest != lastPromptedVersionCode) {
                        lastPromptedVersionCode = latest
                        ui.post { promptUpdate(latestName, apk, notes) }
                    }
                } else if (manual) {
                    ui.post { toast("No update available — you have the latest version.") }
                }
            } catch (e: Exception) {
                // Record every failure (v1.5's manual check failed on
                // Luddy's phone with the cause swallowed); manual checks
                // also show the detail on screen with a Copy button.
                Diag.record(this, "checkForUpdate", e)
                if (manual) {
                    val detail = if (doorErrors.isNotEmpty()) doorErrors.toString().trim()
                        else "${e.javaClass.simpleName}: ${e.message ?: "no detail"}"
                    ui.post { showUpdateFailure(detail) }
                }
            }
        }
    }

    /** Manual update-check failure, with the real cause and a Copy button. */
    private fun showUpdateFailure(detail: String) {
        AlertDialog.Builder(this)
            .setTitle("Update check failed")
            .setMessage(detail + "\n\nCopy this and send it to JMan, and he will fix the real cause.")
            .setPositiveButton("Copy") { _, _ ->
                val cm = getSystemService(android.content.ClipboardManager::class.java)
                cm?.setPrimaryClip(android.content.ClipData.newPlainText("Daily Reports Player update check failure", detail))
                toast("Copied — paste it to JMan.")
            }
            .setNegativeButton("Close", null)
            .show()
    }

    private fun promptUpdate(versionName: String, apk: String, notes: String) {
        AlertDialog.Builder(this)
            .setTitle("Update available")
            .setMessage("Daily Reports Player $versionName is available." +
                if (notes.isNotEmpty()) "\n\n$notes" else "")
            .setPositiveButton("Update") { _, _ -> downloadUpdate(apk) }
            .setNegativeButton("Later", null)
            .show()
    }

    private fun downloadUpdate(apk: String) {
        toast("Downloading update…")
        thread {
            // Two doors, same as the update check (v1.9): the download
            // used to go ONLY through software.aniello.net — the host
            // whose TLS comes back garbled on Luddy's network — so the
            // check could succeed while the download always failed.
            // The ljm3.me door (behind his saved sign-in) goes first.
            val basic = Credentials.authHeader(this)
            val doors = listOf(
                "https://ljm3.me/daily/$apk" to basic,
                APK_BASE + apk to null)
            var lastError: Exception? = null
            var done = false
            for ((door, auth) in doors) {
                try {
                    val dir = File(cacheDir, "updates").apply { mkdirs() }
                    val out = File(dir, "update.apk")
                    val conn = URL(door).openConnection() as HttpURLConnection
                    conn.useCaches = false
                    conn.setRequestProperty("Cache-Control", "no-cache")
                    conn.setRequestProperty("User-Agent", "DailyReportsPlayer Android")
                    if (auth != null) conn.setRequestProperty("Authorization", auth)
                    conn.connectTimeout = 15_000
                    conn.readTimeout = 60_000
                    if (conn.responseCode != 200) throw IllegalStateException("HTTP ${conn.responseCode}")
                    conn.inputStream.use { input -> out.outputStream().use { input.copyTo(it) } }
                    done = true
                    ui.post { installUpdate(out) }
                    break
                } catch (e: Exception) {
                    lastError = e
                    Diag.record(this, "updateDownload door: $door", e)
                }
            }
            if (!done) {
                val detail = "${lastError?.javaClass?.simpleName ?: "Error"}: ${lastError?.message ?: "no detail"}"
                ui.post { showUpdateFailure("The download failed on both routes. Last error: $detail") }
            }
        }
    }

    private fun installUpdate(apkFile: File) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O &&
            !packageManager.canRequestPackageInstalls()
        ) {
            toast("Allow installs from this app to finish the update.")
            startActivity(
                Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:$packageName"))
            )
            return
        }
        // Auto-restart after the update (Luddy, 2026-11-05): an update
        // replaces the package and kills this process, so arm the
        // UpdateRestartReceiver BEFORE installing — it consumes this
        // marker when Android delivers MY_PACKAGE_REPLACED to the new
        // build and relaunches. The install itself goes through the
        // classic installer screen, the route proven on Luddy's phone
        // through 1.12: the PackageInstaller session route tried in
        // 1.13 left the update hanging after the download (the
        // session's prompt never appeared), so it is removed.
        try {
            File(filesDir, "relaunch-after-update").writeText("1")
        } catch (e: Exception) { /* restart net lost; install proceeds */ }
        Diag.phase(this, "installUpdate: opening installer")
        val uri = FileProvider.getUriForFile(this, "$packageName.fileprovider", apkFile)
        val intent = Intent(Intent.ACTION_VIEW)
            .setDataAndType(uri, "application/vnd.android.package-archive")
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        startActivity(intent)
    }

    // -------------------------------------------------------------- Settings

    /**
     * Settings is a button menu (Luddy, 2026-11-05): User rolls up the
     * username/password, Diagnostics holds the error log with Copy
     * and Clear, and Check for Update runs the manual update check.
     * Sign Out and Close sit in the dialog footer.
     */
    private fun showSettings() {
        val box = LinearLayout(this)
        box.orientation = LinearLayout.VERTICAL
        box.setPadding(dp(20), dp(12), dp(20), 0)
        var settingsDialog: AlertDialog? = null
        fun menuButton(text: String, first: Boolean, action: () -> Unit): Button {
            val b = Button(this)
            b.text = text
            b.setOnClickListener {
                settingsDialog?.dismiss()
                action()
            }
            val lp = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            if (!first) lp.topMargin = dp(8)
            b.layoutParams = lp
            return b
        }
        // Menu order (Luddy, 2026-11-05): User, Diagnostics,
        // Background Playback, and Check for Update always last.
        box.addView(menuButton("User", true) { showUserSettings() })
        box.addView(menuButton("Diagnostics", false) { showDiagnostics() })
        box.addView(menuButton("Background Playback", false) { requestBackgroundPlayback() })
        box.addView(menuButton("Check for Update", false) { checkForUpdate(manual = true) })
        settingsDialog = AlertDialog.Builder(this)
            .setTitle("Settings")
            .setView(box)
            .setNeutralButton("Sign Out") { _, _ ->
                controller?.stop()
                restartPlaybackService()
                Credentials.clear(this)
                manifest = null
                dates = emptyList()
                tracks = emptyList()
                showSignIn()
            }
            .setNegativeButton("Close", null)
            .show()
    }

    /**
     * Background Playback (Luddy, 2026-11-05): Samsung's battery
     * manager kills apps it decides are idle, which is how the player
     * sometimes vanished outright while the screen was dark. This
     * asks the system to exempt the player, so it stays alive in the
     * background. (The last-play point covers the rest: even if the
     * player is killed anyway, it now resumes where it stopped.)
     */
    private fun requestBackgroundPlayback() {
        val pm = getSystemService(Context.POWER_SERVICE) as android.os.PowerManager
        if (pm.isIgnoringBatteryOptimizations(packageName)) {
            toast("Background playback is already allowed.")
            return
        }
        try {
            startActivity(
                Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:$packageName"))
            )
        } catch (e: Exception) {
            toast("Open Settings, Apps, Daily Reports Player, Battery, and choose Unrestricted.")
        }
    }

    /** The username / password form, behind the User button. */
    private fun showUserSettings() {
        val box = LinearLayout(this)
        box.orientation = LinearLayout.VERTICAL
        box.setPadding(dp(20), dp(8), dp(20), 0)
        val user = EditText(this)
        user.hint = "Username"
        user.setText(Credentials.username(this) ?: "luddy")
        user.inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
        val pass = EditText(this)
        pass.hint = "Password (leave blank to keep current)"
        pass.inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
        box.addView(user)
        box.addView(pass)
        AlertDialog.Builder(this)
            .setTitle("User")
            .setView(box)
            .setPositiveButton("Save") { _, _ ->
                val u = user.text.toString().trim()
                val p = pass.text.toString()
                val currentPass = Credentials.password(this)
                val finalPass = if (p.isNotEmpty()) p else currentPass
                if (u.isEmpty() || finalPass.isNullOrEmpty()) {
                    toast("Username and password are both required.")
                    return@setPositiveButton
                }
                Credentials.save(this, u, finalPass)
                restartPlaybackService()
                connectController()
                loadManifest()
                showMain()
                toast("Settings saved.")
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    /**
     * The full error log — every recorded error, previous and current
     * (Luddy, 2026-11-05) — scrollable, with Copy and Clear.
     */
    private fun showDiagnostics() {
        val text = Diag.logTail(this, 200_000)
        val view = TextView(this)
        view.text = text
        view.textSize = 11f
        view.setTextColor(SUBTEXT)
        view.setPadding(dp(20), dp(12), dp(20), dp(12))
        val scroll = ScrollView(this)
        scroll.addView(view)
        AlertDialog.Builder(this)
            .setTitle("Diagnostics")
            .setView(scroll)
            .setPositiveButton("Copy") { _, _ ->
                val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                cm.setPrimaryClip(ClipData.newPlainText("diagnostics", text))
                toast("Diagnostics copied.")
            }
            .setNeutralButton("Clear") { _, _ ->
                Diag.clearLog(this)
                Diag.clearCrash(this)
                toast("Diagnostics cleared.")
            }
            .setNegativeButton("Close", null)
            .show()
    }

    // --------------------------------------------------------------- Helpers

    private fun requestNotificationPermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        }
    }

    private fun fmtTime(secs: Double): String {
        val total = secs.toLong().coerceAtLeast(0)
        return String.format(Locale.US, "%d:%02d", total / 60, total % 60)
    }

    private fun prettyDate(iso: String): String {
        return try {
            val inFmt = SimpleDateFormat("yyyy-MM-dd", Locale.US)
            val outFmt = SimpleDateFormat("EEEE, MMMM d, yyyy", Locale.US)
            outFmt.format(inFmt.parse(iso)!!)
        } catch (e: Exception) {
            iso
        }
    }

    /**
     * Two-line date for the day row (Luddy, 2026-11-05): the weekday
     * always stands alone on the first line; the month, zero-padded
     * day, and year sit on the second — e.g. "Thursday" over
     * "November 05, 2026".
     */
    private fun prettyDateStacked(iso: String): String {
        return try {
            val inFmt = SimpleDateFormat("yyyy-MM-dd", Locale.US)
            val parsed = inFmt.parse(iso)!!
            SimpleDateFormat("EEEE", Locale.US).format(parsed) + "\n" +
                SimpleDateFormat("MMMM dd, yyyy", Locale.US).format(parsed)
        } catch (e: Exception) {
            iso
        }
    }

    private fun toast(msg: String) {
        Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()
    }
}
