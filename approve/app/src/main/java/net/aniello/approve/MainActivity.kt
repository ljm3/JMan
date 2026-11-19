package net.aniello.approve

import android.app.Activity
import android.app.AlertDialog
import android.content.ComponentName
import android.content.Intent
import android.content.res.ColorStateList
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.BitmapDrawable
import android.graphics.drawable.GradientDrawable
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
import android.view.WindowInsetsController
import android.view.WindowManager
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.SeekBar
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.core.content.FileProvider
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import androidx.media3.ui.PlayerView
import com.google.common.util.concurrent.ListenableFuture
import com.google.common.util.concurrent.MoreExecutors
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.text.SimpleDateFormat
import java.util.Locale
import kotlin.concurrent.thread

/**
 * Approve — the phone companion to the Approve page (ljm3.me/approve).
 *
 * Everything the site can do, in the app: review episode drafts and
 * Wednesday Content Pre-Approval entries, play the audio (and, when
 * entries carry it, video), read text-only entries (newsletters),
 * read scripts, sources, and artifacts, decide Thursday/Friday
 * placeholders (Include / Leave out), and run the same actions —
 * Adjust / Edit / Approve for episodes, Pre-Adjust / Pre-Edit /
 * Pre-Approve for content — against the same Worker endpoints the
 * page uses, behind the same dashboard sign-in.
 *
 * Built on the Daily Reports Player's lessons: credentials live in
 * the guarded encrypted store with its plain-store fallback; every
 * list load refetches and replaces state wholesale (no stale-queue
 * class of bug); self-update rides ljm3.me behind the sign-in first
 * and software.aniello.net second; failures are recorded by Diag and
 * shown, never swallowed.
 */
class MainActivity : Activity() {

    companion object {
        private const val BASE = "https://ljm3.me"
        private const val ENTRIES_URL = "$BASE/approve/api/entries"
        private const val MEDIA_BASE = "$BASE/approve/media/"
        private const val VERSION_URL_LJM3 = "$BASE/approve/app-version.json"
        private const val VERSION_URL_SOFTWARE = "https://software.aniello.net/apps/approve-version.json"
        private const val APK_BASE_SOFTWARE = "https://software.aniello.net/apps/"

        private val BG = Color.rgb(11, 15, 22)
        private val CARD = Color.rgb(20, 33, 47)
        private val GOLD = Color.rgb(201, 168, 106)
        private val TEXT = Color.rgb(232, 237, 242)
        private val SUB = Color.rgb(159, 179, 200)
        private val LINE = Color.rgb(42, 59, 77)
        private val DANGER = Color.rgb(255, 154, 138)
    }

    // ------------------------------------------------------------ model

    data class Entry(
        val slug: String,
        val kind: String,
        val category: String,
        val title: String,
        val date: String,
        val season: Int?,
        val episode: Int?,
        val durationSecs: Double,
        val filename: String,
        val mediaType: String,
        val description: String,
        val script: String,
        val sources: JSONArray,
        val artifacts: JSONArray,
        val status: String,
        val adjustment: String,
        val approvedAt: String?,
        val preApprovedAt: String?,
        val placeholder: JSONObject?,
        val markers: JSONArray,
        val addedAt: String?,
        val correctedAt: String?,
        val awaitingCorrectionApproval: Boolean
    )

    private fun parseEntry(o: JSONObject): Entry = Entry(
        slug = o.optString("slug"),
        kind = o.optString("kind", "episode"),
        category = o.optString("category"),
        title = o.optString("title"),
        date = o.optString("date"),
        season = if (o.isNull("season")) null else o.optInt("season"),
        episode = if (o.isNull("episode")) null else o.optInt("episode"),
        durationSecs = o.optDouble("duration_secs", 0.0),
        filename = o.optString("filename"),
        mediaType = o.optString("media_type", "text"),
        description = o.optString("description"),
        script = o.optString("script"),
        sources = o.optJSONArray("sources") ?: JSONArray(),
        artifacts = o.optJSONArray("artifacts") ?: JSONArray(),
        status = o.optString("status", "pending"),
        adjustment = o.optString("adjustment"),
        approvedAt = if (o.isNull("approved_at")) null else o.optString("approved_at"),
        preApprovedAt = if (o.isNull("pre_approved_at")) null else o.optString("pre_approved_at"),
        placeholder = if (o.isNull("placeholder")) null else o.optJSONObject("placeholder"),
        markers = o.optJSONArray("markers") ?: JSONArray(),
        addedAt = if (o.isNull("added_at")) null else o.optString("added_at"),
        correctedAt = if (o.isNull("corrected_at")) null else o.optString("corrected_at"),
        awaitingCorrectionApproval = o.optBoolean("awaiting_correction_approval", false)
    )

    // ------------------------------------------------------------ state

    private lateinit var root: FrameLayout
    private var entries: List<Entry> = emptyList()
    private var detailSlug: String? = null
    private var resumeRefreshArmed = false
    private var controllerFuture: ListenableFuture<MediaController>? = null
    private var controller: MediaController? = null
    private var playerView: PlayerView? = null
    private var seekBar: SeekBar? = null
    private var playButton: Button? = null
    private var stripTime: TextView? = null
    private var seeking = false
    private var positionLabel: TextView? = null
    private val artworkMem = mutableMapOf<String, ByteArray>()
    private val tickerHandler = Handler(Looper.getMainLooper())
    private var ticker: Runnable? = null
    private var lastUpdateCheckMs = 0L
    private var lastPromptedVersionCode = 0L

    private val ui get() = window.decorView

    private fun post(block: () -> Unit) = runOnUiThread(block)

    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).toInt()

    private fun toast(msg: String) =
        Toast.makeText(this, msg, Toast.LENGTH_LONG).show()

    // ------------------------------------------------------------ lifecycle

    override fun onCreate(savedInstanceState: Bundle?) {
        Diag.install(this)
        super.onCreate(savedInstanceState)
        try {
            root = FrameLayout(this).apply { setBackgroundColor(BG) }
            setContentView(root)
            // Luddy (2026-11-08): the status bar stays visible at all
            // times — he identifies the time marker of any issue from
            // the screen (the always-on position readout under the
            // player carries that marker; the system bar stays up too).
            window.clearFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN)
            window.addFlags(WindowManager.LayoutParams.FLAG_DRAWS_SYSTEM_BAR_BACKGROUNDS)
            window.statusBarColor = BG
            // Light icons on the dark bar, set explicitly (Luddy,
            // 2026-11-08: the bar "appears odd" when its appearance is
            // left to the deprecated visibility flag). The bar itself
            // is never hidden by the app — it goes away only with the
            // phone's lock screen, screen saver, or true full screen.
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                window.insetsController?.setSystemBarsAppearance(
                    0, WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS)
            } else {
                @Suppress("DEPRECATION")
                window.decorView.systemUiVisibility =
                    window.decorView.systemUiVisibility and
                        View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR.inv()
            }
            Diag.phase(this, "app starting, version " + BuildConfig.VERSION_NAME)
            showCrashIfAny()
            connectController()
            if (Credentials.hasCredentials(this)) {
                showLoading("Loading what awaits your approval…")
                refreshEntries { ok ->
                    if (ok) showList() else showSignIn("Your saved sign-in was not accepted. Please sign in again.")
                }
            } else {
                showSignIn(null)
            }
            checkForUpdate(manual = false)
            resumeRefreshArmed = true
        } catch (e: Exception) {
            Diag.record(this, "MainActivity.onCreate", e)
            throw e
        }
    }

    override fun onResume() {
        super.onResume()
        // v1.17: automatic update check on return to the foreground
        // too, mirroring the Daily Reports Player — at launch alone,
        // the prompt never reached Luddy (the player's own v1.4
        // lesson, 2026-11-05): this app's process lives across uses,
        // so onCreate's check effectively never re-ran and the
        // v1.14–v1.16 updates never offered themselves. The existing
        // 60-second throttle and once-per-version prompt rule keep
        // it from nagging.
        checkForUpdate(manual = false)
        // v1.13: returning to the app re-fetches the entries, so a
        // state change that landed while the app was in the background
        // (corrected audio re-listed, an approval recorded) shows
        // without a manual Refresh. The list re-renders only when it
        // is the screen showing; an open entry keeps its screen and
        // picks up the fresh data when it is reopened.
        if (!resumeRefreshArmed || !Credentials.hasCredentials(this)) return
        refreshEntries { ok ->
            if (ok && detailSlug == null) showList()
        }
    }

    override fun onDestroy() {
        stopPositionTicker()
        controllerFuture?.let { MediaController.releaseFuture(it) }
        controllerFuture = null
        controller = null
        super.onDestroy()
    }

    private fun connectController() {
        val token = SessionToken(this, ComponentName(this, PlaybackService::class.java))
        val future = MediaController.Builder(this, token).buildAsync()
        controllerFuture = future
        future.addListener({
            try {
                val c = future.get()
                controller = c
                playerView?.player = c
                // Keep the readout honest on playback events as well
                // as on the ticker (v1.4 fix — Luddy, 2026-11-08: "no
                // time elapsing"). Playback errors are surfaced, not
                // silent (v1.7): through v1.6 an error left every
                // visual frozen with no explanation on screen.
                c.addListener(object : Player.Listener {
                    override fun onIsPlayingChanged(isPlaying: Boolean) =
                        updatePositionLabel()
                    override fun onPlaybackStateChanged(state: Int) =
                        updatePositionLabel()
                    override fun onPositionDiscontinuity(
                        oldPosition: Player.PositionInfo,
                        newPosition: Player.PositionInfo, reason: Int) =
                        updatePositionLabel()
                    override fun onPlayerError(error: PlaybackException) {
                        val msg = error.message ?: error.errorCodeName
                        positionLabel?.text = "Playback problem: $msg"
                        toast("Playback problem: $msg")
                        Diag.record(this@MainActivity, "playerError", error)
                    }
                })
                // If an entry was opened before the controller
                // connected, its media was never loaded — load it now
                // and start the readout ticker.
                val slug = detailSlug
                if (slug != null) {
                    entries.firstOrNull { it.slug == slug }?.let { e ->
                        if (e.mediaType != "text" && e.filename.isNotEmpty()) {
                            loadMediaFor(e, c)
                            if (positionLabel != null) startPositionTicker()
                        }
                    }
                }
            } catch (e: Exception) {
                Diag.record(this, "connectController", e)
            }
        }, MoreExecutors.directExecutor())
    }

    private val logoBytes: ByteArray by lazy {
        resources.openRawResource(R.drawable.ae_logo).use { it.readBytes() }
    }

    /** Point the session player at an entry's media (idempotent). */
    private fun loadMediaFor(e: Entry, c: MediaController) {
        val url = MEDIA_BASE + e.filename
        val currentUri = c.currentMediaItem?.localConfiguration?.uri?.toString()
        if (currentUri == url) return
        // The show's own cover art when it is already fetched,
        // otherwise the AE logo (Luddy, 2026-11-08: "It should show
        // the show logo").
        val art = coverUrlFor(e.category)?.let { artworkMem[it] } ?: logoBytes
        val item = MediaItem.Builder()
            .setUri(url)
            .setMediaMetadata(
                MediaMetadata.Builder()
                    .setTitle(e.title)
                    .setArtist(e.category)
                    .setArtworkData(art, MediaMetadata.PICTURE_TYPE_FRONT_COVER)
                    .build())
            .build()
        c.setMediaItem(item)
        c.prepare()
    }

    /** Canonical show cover art (the feeds' images) by category. */
    private fun coverUrlFor(category: String): String? = when (category) {
        "AI Weekly PopTop" ->
            "https://podcasts.aniello.net/covers/ai-weekly-cover.png"
        "Data Center Weekly PopTop" ->
            "https://podcasts.aniello.net/covers/data-center-weekly-cover.png"
        "AI Law Weekly PopTop" ->
            "https://podcasts.aniello.net/covers/ai-law-weekly-cover.png"
        "Data Center Law Weekly PopTop" ->
            "https://podcasts.aniello.net/covers/data-center-law-weekly-cover.png"
        "Treasury Weekly PopTop" ->
            "https://podcasts.aniello.net/covers/treasury-weekly-cover.png"
        "Banking Weekly PopTop" ->
            "https://podcasts.aniello.net/covers/banking-weekly-cover.png"
        else -> null
    }

    /**
     * Fetch an entry's show cover (public, no sign-in), downscaled to
     * 512px and cached in memory and on disk. Calls back on the UI
     * thread with the PNG bytes, or null when the show has no cover
     * or the fetch failed (callers fall back to the AE logo).
     */
    private fun fetchArtwork(e: Entry, onReady: (ByteArray?) -> Unit) {
        val url = coverUrlFor(e.category)
        if (url == null) { onReady(null); return }
        artworkMem[url]?.let { onReady(it); return }
        val disk = File(cacheDir, "art-" + url.substringAfterLast('/'))
        if (disk.exists()) {
            try {
                val b = disk.readBytes()
                artworkMem[url] = b
                onReady(b)
                return
            } catch (_: Exception) { /* fall through to the network */ }
        }
        thread {
            var out: ByteArray? = null
            try {
                val conn = URL(url).openConnection() as HttpURLConnection
                conn.connectTimeout = 15000
                conn.readTimeout = 30000
                val raw = conn.inputStream.use { it.readBytes() }
                val bmp = BitmapFactory.decodeByteArray(raw, 0, raw.size)
                if (bmp != null) {
                    val scale = 512f / maxOf(bmp.width, bmp.height)
                    val scaled = if (scale < 1f)
                        Bitmap.createScaledBitmap(bmp,
                            (bmp.width * scale).toInt().coerceAtLeast(1),
                            (bmp.height * scale).toInt().coerceAtLeast(1), true)
                    else bmp
                    val baos = java.io.ByteArrayOutputStream()
                    scaled.compress(Bitmap.CompressFormat.PNG, 100, baos)
                    out = baos.toByteArray()
                    try { disk.writeBytes(out) } catch (_: Exception) { }
                }
            } catch (_: Exception) { out = null }
            val result = out
            if (result != null) artworkMem[url] = result
            post { onReady(result) }
        }
    }

    /**
     * Refresh the position displays from the controller: the pinned
     * footer readout, the control strip's own time text, and the
     * strip's progress bar. Mirrors the Daily Reports Player's
     * refreshProgress (Luddy, 2026-11-08: "Controls should be the
     * same as on the daily player") — the bar runs on a fixed
     * 0–1000 scale with the position scaled into it, and durations
     * fall back to the entry's own duration from the list when the
     * player has not reported one.
     */
    private fun updatePositionLabel() {
        val c = controller ?: return
        val posMs = c.currentPosition
        var durMs = if (c.duration > 0) c.duration else 0L
        if (durMs <= 0) {
            val slug = detailSlug
            val e = if (slug != null) entries.firstOrNull { it.slug == slug } else null
            if (e != null && e.durationSecs > 0)
                durMs = (e.durationSecs * 1000).toLong()
        }
        val timeText = if (durMs > 0)
            "${fmtDuration(posMs / 1000.0)} / ${fmtDuration(durMs / 1000.0)}"
        else fmtDuration(posMs / 1000.0)
        positionLabel?.text = timeText
        stripTime?.text = timeText
        if (!seeking) seekBar?.let { bar ->
            bar.progress = if (durMs > 0) (posMs * 1000 / durMs).toInt() else 0
        }
        playButton?.text = if (c.isPlaying) "⏸" else "▶"
    }

    private fun showCrashIfAny() {
        val summary = Diag.lastCrashSummary(this) ?: return
        val full = Diag.lastCrashFull(this) ?: summary
        AlertDialog.Builder(this)
            .setTitle("The app hit a problem last time")
            .setMessage(summary + "\n\nCopy this and send it to JMan, and he will fix the real cause.")
            .setPositiveButton("Copy") { _, _ ->
                val cm = getSystemService(android.content.ClipboardManager::class.java)
                cm?.setPrimaryClip(android.content.ClipData.newPlainText("Approve crash", full))
                Diag.clearCrash(this)
                toast("Copied — paste it to JMan.")
            }
            .setNegativeButton("Dismiss") { _, _ -> Diag.clearCrash(this) }
            .show()
    }

    // ------------------------------------------------------------ HTTP

    private data class HttpResult(val code: Int, val body: String)

    private fun http(method: String, url: String, json: JSONObject?): HttpResult {
        val conn = URL(url).openConnection() as HttpURLConnection
        conn.requestMethod = method
        conn.useCaches = false
        conn.setRequestProperty("Cache-Control", "no-cache")
        conn.setRequestProperty("User-Agent", "Approve/" + BuildConfig.VERSION_NAME)
        Credentials.authHeader(this)?.let { conn.setRequestProperty("Authorization", it) }
        conn.connectTimeout = 15_000
        conn.readTimeout = 30_000
        if (json != null) {
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", "application/json")
            conn.outputStream.use { it.write(json.toString().toByteArray(Charsets.UTF_8)) }
        }
        val code = conn.responseCode
        val stream = if (code in 200..299) conn.inputStream else conn.errorStream
        val body = try { stream?.bufferedReader()?.readText() ?: "" } catch (e: Exception) { "" }
        return HttpResult(code, body)
    }

    /** Fetch the full entry list; replaces local state wholesale. */
    private fun refreshEntries(done: (Boolean) -> Unit) {
        thread {
            try {
                val r = http("GET", ENTRIES_URL, null)
                if (r.code == 401) { post { done(false) }; return@thread }
                if (r.code != 200) throw IllegalStateException("Entries answered HTTP ${r.code}")
                val arr = JSONObject(r.body).optJSONArray("entries") ?: JSONArray()
                val list = ArrayList<Entry>()
                for (i in 0 until arr.length()) list.add(parseEntry(arr.getJSONObject(i)))
                // Newest first — what arrived most recently sits on top.
                list.sortByDescending { it.addedAt ?: it.date }
                entries = list
                // Warm the show-cover cache in the background so an
                // entry's artwork (badge + notification) is ready the
                // moment it is opened.
                for (en in list) {
                    if (en.mediaType != "text" && coverUrlFor(en.category) != null) {
                        fetchArtwork(en) { }
                    }
                }
                post { done(true) }
            } catch (e: Exception) {
                Diag.record(this, "refreshEntries", e)
                post {
                    toast("Could not load the list: ${e.message ?: "network error"}")
                    done(entries.isNotEmpty())
                }
            }
        }
    }

    private fun postAction(path: String, payload: JSONObject, what: String) {
        thread {
            try {
                val r = http("POST", BASE + path, payload)
                val obj = try { JSONObject(r.body) } catch (e: Exception) { JSONObject() }
                if (r.code == 401) {
                    post { showSignIn("Your sign-in was not accepted. Please sign in again.") }
                    return@thread
                }
                if (r.code != 200 || !obj.optBoolean("ok", false)) {
                    val err = obj.optString("error", "HTTP ${r.code}")
                    throw IllegalStateException("$what failed: $err")
                }
                post {
                    toast("$what recorded.")
                    refreshEntries { ok ->
                        if (ok) {
                            val slug = detailSlug
                            if (slug != null && entries.any { it.slug == slug }) showDetail(slug) else showList()
                        }
                    }
                }
            } catch (e: Exception) {
                Diag.record(this, "postAction $path", e)
                post { toast(e.message ?: "$what failed.") }
            }
        }
    }

    // ------------------------------------------------------------ shared views

    private fun textView(text: String, size: Float, color: Int, bold: Boolean = false): TextView =
        TextView(this).apply {
            this.text = text
            textSize = size
            setTextColor(color)
            if (bold) setTypeface(typeface, Typeface.BOLD)
        }

    /**
     * Transport button in the Daily Reports Player's control style
     * (13.5sp, 43dp minimum height, rounded filled background) —
     * the player's blue primary becomes this app's gold.
     */
    private fun controlButton(text: String, primary: Boolean = false): Button {
        val b = Button(this)
        b.text = text
        b.textSize = 13.5f
        b.minHeight = dp(43)
        b.minimumHeight = dp(43)
        b.isAllCaps = false
        b.setTextColor(if (primary) BG else Color.WHITE)
        val bg = android.graphics.drawable.GradientDrawable()
        bg.setColor(if (primary) GOLD else Color.parseColor("#1b2637"))
        bg.setStroke(dp(1), if (primary) GOLD else Color.rgb(52, 68, 88))
        bg.cornerRadius = dp(10).toFloat()
        b.background = bg
        b.minWidth = 0
        b.minimumWidth = 0
        return b
    }

    private fun actionButton(label: String, primary: Boolean, rounded: Boolean = false): Button =
        Button(this).apply {
            text = label
            isAllCaps = false
            textSize = 15f
            if (primary) {
                if (rounded) {
                    background = GradientDrawable().apply {
                        setColor(GOLD)
                        cornerRadius = dp(10).toFloat()
                    }
                } else {
                    setBackgroundColor(GOLD)
                }
                setTextColor(Color.rgb(11, 15, 22))
            } else {
                if (rounded) {
                    background = GradientDrawable().apply {
                        setColor(CARD)
                        cornerRadius = dp(10).toFloat()
                    }
                } else {
                    setBackgroundColor(CARD)
                }
                setTextColor(TEXT)
            }
        }

    /**
     * Pill-shaped button with a border — the Daily Reports Player's
     * Settings-button format (Luddy, 2026-11-08): fully rounded, thin
     * border in the normal state; the highlight shows only at the
     * tap — while pressed, the border thickens and turns gold, the
     * fill warms, and the text turns gold. Nothing stays highlighted.
     */
    private fun pillButton(text: String): Button {
        fun shape(fill: Int, strokeColor: Int, strokeWidth: Int): GradientDrawable {
            val g = GradientDrawable()
            g.shape = GradientDrawable.RECTANGLE
            g.cornerRadius = dp(999).toFloat()
            g.setColor(fill)
            g.setStroke(dp(strokeWidth), strokeColor)
            return g
        }
        val bg = StateListDrawable()
        bg.addState(intArrayOf(android.R.attr.state_pressed), shape(Color.parseColor("#2a2118"), GOLD, 2))
        bg.addState(intArrayOf(), shape(CARD, LINE, 1))
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

    /** Installed version line — the Daily app's format, read from the
     * package so it can never drift from the build on the phone. */
    private fun versionLine(): TextView {
        val versionText = try {
            "Version:  " + (packageManager.getPackageInfo(packageName, 0).versionName ?: "")
        } catch (e: Exception) { "" }
        val v = textView(versionText, 12f, SUB)
        v.includeFontPadding = false
        v.setPadding(0, 0, 0, 0)
        return v
    }

    private fun divider(): View = View(this).apply {
        setBackgroundColor(LINE)
        layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(1)).apply {
            topMargin = dp(10); bottomMargin = dp(10)
        }
    }

    // The always-on playback position readout (Luddy, 2026-11-08):
    // a half-second ticker mirrors the controller's position into the
    // detail screen's readout, which never auto-hides the way the
    // player controller does.
    private fun startPositionTicker() {
        // NOTE: do NOT route this through stopPositionTicker() — that
        // method nulls positionLabel for teardown, and doing it here
        // killed the readout in v1.1–v1.3: the ticker ran, but the
        // label it was supposed to update had just been nulled, so no
        // time ever elapsed on screen (Luddy, 2026-11-08).
        ticker?.let { tickerHandler.removeCallbacks(it) }
        val r = object : Runnable {
            override fun run() {
                if (positionLabel != null) updatePositionLabel()
                tickerHandler.postDelayed(this, 500)
            }
        }
        ticker = r
        tickerHandler.post(r)
    }

    private fun stopPositionTicker() {
        ticker?.let { tickerHandler.removeCallbacks(it) }
        ticker = null
        positionLabel = null
    }

    private fun showLoading(msg: String) {
        stopPositionTicker()
        root.removeAllViews()
        val tv = textView(msg, 16f, SUB)
        tv.gravity = Gravity.CENTER
        root.addView(tv, FrameLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
    }

    private fun fmtDate(iso: String): String {
        if (iso.isEmpty()) return ""
        return try {
            val inFmt = SimpleDateFormat("yyyy-MM-dd", Locale.US)
            val outFmt = SimpleDateFormat("EEEE, MMMM d, yyyy", Locale.US)
            outFmt.format(inFmt.parse(iso)!!)
        } catch (e: Exception) { iso }
    }

    private fun fmtDuration(secs: Double): String {
        val total = secs.toInt()
        return "%d:%02d".format(total / 60, total % 60)
    }

    private fun kindLabel(e: Entry): String = when (e.kind) {
        "content" -> "Content Pre-Approval"
        "newsletter" -> "Newsletter"
        else -> if (e.mediaType == "text") "Text" else "Episode"
    }

    // Correction state (Luddy, 2026-11-09): corrected audio re-listed
    // after a markers adjustment is "Awaiting Correction Approval"
    // while the entry is still pending; the state clears server-side
    // on approval or on a fresh adjustment. v1.13: the status label
    // itself carries this state — before, the label kept reading
    // "Adjustment requested" alongside a separate badge, so the app
    // still showed the adjustment as outstanding after the corrected
    // audio had been delivered.
    private fun awaitingCorrection(e: Entry): Boolean =
        e.awaitingCorrectionApproval && e.status != "approved" && e.status != "pre-approved"

    private fun statusLabel(e: Entry): String {
        if (awaitingCorrection(e)) return "Awaiting Correction Approval"
        return when (e.status) {
            "approved" -> "Approved — Publish Commenced"
            "pre-approved" -> "Pre-Approved"
            "adjustment-requested" -> if (e.kind == "content") "Pre-Adjustment Requested" else "Adjustment Requested"
            else -> if (e.kind == "content") "Awaiting Pre-Approval" else "Awaiting Your Approval"
        }
    }

    private fun metaLine(e: Entry): String {
        val bits = ArrayList<String>()
        if (e.season != null && e.episode != null) bits.add("Season ${e.season}, Episode ${e.episode}")
        else if (e.episode != null) bits.add("Episode ${e.episode}")
        if (e.date.isNotEmpty()) bits.add(fmtDate(e.date))
        if (e.mediaType != "text" && e.durationSecs > 0) bits.add(fmtDuration(e.durationSecs))
        return bits.joinToString(" · ")
    }

    // ------------------------------------------------------------ sign-in

    private fun showSignIn(error: String?) {
        stopPositionTicker()
        detailSlug = null
        root.removeAllViews()
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(24), dp(32), dp(24), dp(24))
        }
        val logo = ImageView(this).apply {
            setImageResource(R.drawable.ae_logo)
            layoutParams = LinearLayout.LayoutParams(dp(72), dp(64))
        }
        col.addView(logo)
        col.addView(textView("Approve Player", 26f, TEXT, bold = true).apply {
            setPadding(0, dp(12), 0, dp(4))
        })
        col.addView(versionLine())
        col.addView(textView(
            "Everything awaiting your approval — the same list as ljm3.me/approve, " +
                "behind the same dashboard sign-in.", 15f, SUB))
        if (error != null) {
            col.addView(textView(error, 15f, DANGER).apply { setPadding(0, dp(14), 0, 0) })
        }
        val user = EditText(this).apply {
            hint = "Username"
            setText(Credentials.username(this@MainActivity) ?: "luddy")
            setTextColor(TEXT); setHintTextColor(SUB)
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
        }
        val pass = EditText(this).apply {
            hint = "Password"
            setText(Credentials.password(this@MainActivity) ?: "")
            setTextColor(TEXT); setHintTextColor(SUB)
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
        }
        col.addView(user, LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
            topMargin = dp(20)
        })
        col.addView(pass, LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
            topMargin = dp(10)
        })
        val signIn = actionButton("Sign In", primary = true)
        signIn.setOnClickListener {
            val u = user.text.toString().trim()
            val p = pass.text.toString()
            if (u.isEmpty() || p.isEmpty()) { toast("Enter both the username and the password."); return@setOnClickListener }
            Credentials.save(this, u, p)
            showLoading("Signing in…")
            refreshEntries { ok ->
                if (ok) showList() else showSignIn("That sign-in was not accepted. Check the password and try again.")
            }
        }
        col.addView(signIn, LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
            topMargin = dp(18)
        })
        val scroll = ScrollView(this).apply { addView(col) }
        root.addView(scroll)
    }

    // ------------------------------------------------------------ list

    private fun showList() {
        stopPositionTicker()
        detailSlug = null
        root.removeAllViews()
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(16), dp(16), dp(16))
        }
        // Header — the Daily Reports Player's layout (Luddy,
        // 2026-11-08): the AE logo at the left; the title + version
        // form a tight two-line stack in their own full-height column
        // with center gravity, so the pair's center sits on the
        // logo's center; the Options pill rides at the right end of
        // the header row, formatted like the Daily app's Settings
        // button, and opens the menu holding the button options.
        val header = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        val logo = ImageView(this).apply {
            setImageResource(R.drawable.ae_logo)
            adjustViewBounds = true
            layoutParams = LinearLayout.LayoutParams(dp(56), dp(56))
        }
        header.addView(logo)
        val headerCol = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_VERTICAL
            layoutParams = LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.MATCH_PARENT, 1f).apply {
                leftMargin = dp(10)
            }
        }
        headerCol.addView(textView("Approve Player", 20f, GOLD, bold = true).apply {
            includeFontPadding = false
        })
        headerCol.addView(versionLine().apply {
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                topMargin = dp(1)
            }
        })
        header.addView(headerCol)
        val optionsBtn = pillButton("Options")
        optionsBtn.setOnClickListener { showOptionsMenu() }
        header.addView(optionsBtn)
        col.addView(header)
        col.addView(textView(
            if (entries.isEmpty()) "Nothing is awaiting approval right now."
            else "${entries.size} item${if (entries.size == 1) "" else "s"} awaiting your review.",
            14f, SUB).apply { setPadding(0, dp(8), 0, dp(6)) })

        for (e in entries) {
            col.addView(entryCard(e))
        }

        val scroll = ScrollView(this).apply { addView(col) }
        root.addView(scroll)
    }

    /**
     * Options is a button menu (Luddy, 2026-11-08), mirroring the
     * Daily app's Settings menu: User first (the sign-in screen,
     * renamed from Account — Change Sign-In and moved to the top at
     * Luddy's direction), then Refresh and Check for Updates; Close
     * in the dialog footer.
     */
    private fun showOptionsMenu() {
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(12), dp(20), 0)
        }
        var dialog: AlertDialog? = null
        fun menuButton(text: String, first: Boolean, action: () -> Unit): Button {
            val b = Button(this)
            b.text = text
            b.isAllCaps = false
            b.setOnClickListener {
                dialog?.dismiss()
                action()
            }
            val lp = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            if (!first) lp.topMargin = dp(8)
            b.layoutParams = lp
            return b
        }
        box.addView(menuButton("User", true) { showSignIn(null) })
        box.addView(menuButton("Refresh", false) {
            showLoading("Refreshing…")
            refreshEntries { showList() }
        })
        box.addView(menuButton("Check for Updates", false) { checkForUpdate(manual = true) })
        dialog = AlertDialog.Builder(this)
            .setTitle("Options")
            .setView(box)
            .setNegativeButton("Close", null)
            .show()
    }

    private fun entryCard(e: Entry): View {
        val card = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(CARD)
            setPadding(dp(14), dp(12), dp(14), dp(12))
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                topMargin = dp(10)
            }
            isClickable = true
            setOnClickListener { showDetail(e.slug) }
        }
        val top = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        top.addView(textView(kindLabel(e), 12f, GOLD, bold = true).apply {
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        })
        val corr = awaitingCorrection(e)
        top.addView(textView(statusLabel(e), 12f, if (corr) GOLD else SUB, bold = corr))
        card.addView(top)
        if (e.kind == "content") {
            card.addView(textView(e.category, 13f, SUB).apply { setPadding(0, dp(6), 0, 0) })
            card.addView(textView("“${e.title}”", 17f, TEXT, bold = true).apply {
                setPadding(0, dp(2), 0, 0)
            })
        } else {
            card.addView(textView(e.title, 17f, TEXT, bold = true).apply {
                setPadding(0, dp(6), 0, 0)
            })
            if (e.category.isNotEmpty()) {
                card.addView(textView(e.category, 13f, SUB).apply { setPadding(0, dp(2), 0, 0) })
            }
        }
        val meta = metaLine(e)
        if (meta.isNotEmpty()) {
            card.addView(textView(meta, 13f, SUB).apply { setPadding(0, dp(4), 0, 0) })
        }
        val ph = e.placeholder
        if (ph != null && ph.optString("headline").isNotEmpty() && !ph.has("decision")) {
            card.addView(textView("A Thursday/Friday story awaits your decision.", 13f, GOLD).apply {
                setPadding(0, dp(6), 0, 0)
            })
        }
        return card
    }

    // ------------------------------------------------------------ detail

    private fun showDetail(slug: String) {
        val e = entries.firstOrNull { it.slug == slug } ?: run { showList(); return }
        detailSlug = slug
        stopPositionTicker()
        root.removeAllViews()
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(16), dp(16), dp(16))
        }
        val back = pillButton("‹ Back to the List")
        back.setOnClickListener { showList() }
        col.addView(back, LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT))

        val headRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(0, dp(14), 0, 0)
        }
        headRow.addView(textView(kindLabel(e), 12f, GOLD, bold = true).apply {
            layoutParams = LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        })
        col.addView(headRow)
        if (e.kind == "content") {
            col.addView(textView("Content Pre-Approval - ${e.category}", 20f, TEXT, bold = true).apply {
                setPadding(0, dp(4), 0, 0)
            })
            col.addView(textView("“${e.title}”", 17f, TEXT).apply { setPadding(0, dp(2), 0, 0) })
        } else {
            col.addView(textView(e.title, 20f, TEXT, bold = true).apply {
                setPadding(0, dp(4), 0, 0)
            })
            if (e.category.isNotEmpty()) {
                col.addView(textView(e.category, 14f, SUB).apply { setPadding(0, dp(2), 0, 0) })
            }
        }
        val meta = metaLine(e)
        if (meta.isNotEmpty()) {
            col.addView(textView(meta, 13f, SUB).apply { setPadding(0, dp(4), 0, 0) })
        }
        col.addView(textView(statusLabel(e), 14f, GOLD, bold = true).apply {
            setPadding(0, dp(8), 0, 0)
        })

        // Media — audio and video share one player; text entries have none.
        stopPositionTicker()
        playerView = null
        seekBar = null
        playButton = null
        stripTime = null
        var detailFooter: LinearLayout? = null
        if (e.mediaType != "text" && e.filename.isNotEmpty()) {
            // The window itself holds ONLY the visual (Luddy,
            // 2026-11-08): no controls and no status bar inside it.
            // The transport controls and the progress bar live in a
            // separate control view directly BELOW the window.
            val pv = PlayerView(this).apply {
                setBackgroundColor(Color.BLACK)
                useController = false
                // Fallback visual until the show cover lands: the AE
                // logo (Luddy, 2026-11-08).
                defaultArtwork =
                    androidx.core.content.ContextCompat.getDrawable(
                        this@MainActivity, R.drawable.ae_logo)
            }
            pv.player = controller
            playerView = pv
            if (e.mediaType == "video") {
                pv.layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, dp(240)).apply {
                    topMargin = dp(12)
                }
                col.addView(pv)
            } else {
                // Audio window: just the show's logo image in the
                // window, plain (Luddy, 2026-11-08 — a spinning
                // shield was tried and dropped the same day). The
                // player's artwork IS the image: the show cover
                // replaces the AE logo placeholder as soon as it is
                // fetched.
                pv.layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, dp(200)).apply {
                    topMargin = dp(12)
                }
                col.addView(pv)
                fetchArtwork(e) { bytes ->
                    if (bytes != null && detailSlug == e.slug) {
                        val bmp = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                        if (bmp != null) {
                            pv.defaultArtwork = BitmapDrawable(resources, bmp)
                        }
                    }
                }
            }
            // Controls below the window, mirroring the Daily
            // Reports Player's Now Playing panel (Luddy,
            // 2026-11-08: "Controls should be the same as on the
            // daily player"): a time line at the right, the
            // progress bar on a fixed 0–1000 scale, and the
            // five-button row (previous, −15s, play/pause, +15s,
            // next) — same glyphs, sizes, and behavior as the
            // player; the strip's time text counts with playback
            // right where the bar is. The library control view
            // (v1.6) and the first app-built bar (v1.7) did not
            // move on Luddy's phone; this is the player's proven
            // arrangement, driven by updatePositionLabel.
            val strip = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                setBackgroundColor(CARD)
                setPadding(dp(10), dp(8), dp(10), dp(10))
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT)
            }
            val timeRow = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
            }
            val timeSpacer = View(this)
            timeRow.addView(timeSpacer, LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
            val st = textView("0:00 / 0:00", 13f, SUB)
            stripTime = st
            timeRow.addView(st)
            strip.addView(timeRow)
            val bar = SeekBar(this).apply {
                max = 1000
                setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
                    override fun onProgressChanged(
                        sb: SeekBar?, progress: Int, fromUser: Boolean) { }
                    override fun onStartTrackingTouch(sb: SeekBar?) {
                        seeking = true
                    }
                    override fun onStopTrackingTouch(sb: SeekBar?) {
                        seeking = false
                        val cc = controller ?: return
                        var durMs = if (cc.duration > 0) cc.duration else 0L
                        if (durMs <= 0 && e.durationSecs > 0)
                            durMs = (e.durationSecs * 1000).toLong()
                        if (durMs > 0)
                            cc.seekTo((sb?.progress ?: 0).toLong() * durMs / 1000)
                        updatePositionLabel()
                    }
                })
            }
            seekBar = bar
            strip.addView(bar, LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                topMargin = dp(4)
            })
            val controls = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                    topMargin = dp(6)
                }
            }
            val prevB = controlButton("⏮")
            val backB = controlButton("−15s")
            val playB = controlButton("▶", primary = true)
            val fwdB = controlButton("+15s")
            val nextB = controlButton("⏭")
            playButton = playB
            for (b in listOf(prevB, backB, playB, fwdB, nextB)) {
                val blp = LinearLayout.LayoutParams(
                    0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
                blp.leftMargin = dp(3)
                blp.rightMargin = dp(3)
                b.layoutParams = blp
                controls.addView(b)
            }
            prevB.setOnClickListener {
                controller?.seekToPreviousMediaItem()
                updatePositionLabel()
            }
            nextB.setOnClickListener {
                controller?.seekToNextMediaItem()
                updatePositionLabel()
            }
            backB.setOnClickListener {
                controller?.let {
                    it.seekTo(maxOf(0, it.currentPosition - 15_000))
                }
                updatePositionLabel()
            }
            fwdB.setOnClickListener {
                controller?.let { it.seekTo(it.currentPosition + 15_000) }
                updatePositionLabel()
            }
            playB.setOnClickListener {
                val cc = controller ?: return@setOnClickListener
                if (cc.playbackState == Player.STATE_IDLE ||
                    cc.playbackState == Player.STATE_ENDED) {
                    loadMediaFor(e, cc)
                    cc.prepare()
                    cc.play()
                } else if (cc.isPlaying) cc.pause() else cc.play()
                updatePositionLabel()
            }
            strip.addView(controls)
            col.addView(strip)
            controller?.let { loadMediaFor(e, it) }
            val dl = textView("Download the file", 13f, GOLD).apply {
                setPadding(0, dp(8), 0, 0)
                isClickable = true
                setOnClickListener {
                    startActivity(Intent(Intent.ACTION_VIEW,
                        Uri.parse(MEDIA_BASE + e.filename + "?download=1")))
                }
            }
            col.addView(dl)

            // Status line + Mark button — pinned at the BOTTOM of
            // the content view (Luddy, 2026-11-08): the position
            // readout sits directly above the Mark button in a fixed
            // footer under the scrolling content, so the time marker
            // of any issue is always on screen without scrolling —
            // it leaves only with the phone's lock screen, screen
            // saver, or true full screen, like the status bar.
            val decidedEarly = e.status == "approved" || e.status == "pre-approved"
            val footer = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                setBackgroundColor(BG)
                setPadding(dp(16), dp(10), dp(16), dp(12))
            }
            footer.addView(View(this).apply {
                setBackgroundColor(LINE)
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, dp(1))
            })
            val posLabel = textView("0:00", 20f, TEXT, bold = true).apply {
                setPadding(0, dp(8), 0, 0)
                gravity = Gravity.CENTER
            }
            positionLabel = posLabel
            footer.addView(posLabel)
            val markBtn = actionButton("⚑ Mark issue at this position", primary = false, rounded = true).apply {
                isEnabled = !decidedEarly
                setOnClickListener { showMarkerDialog(e) }
            }
            footer.addView(markBtn, LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                topMargin = dp(8)
            })
            detailFooter = footer
            if (e.markers.length() > 0) {
                col.addView(textView("Markers", 13f, GOLD, bold = true).apply {
                    setPadding(0, dp(12), 0, 0)
                })
                for (i in 0 until e.markers.length()) {
                    val m = e.markers.optJSONObject(i) ?: continue
                    col.addView(markerRow(e, m, decidedEarly))
                    // Found/Done resolutions (Luddy, 2026-11-09): one
                    // line per correction pass under the marker,
                    // oldest first — mirrors the Approve page.
                    val res = m.optJSONArray("resolutions")
                    if (res != null) {
                        for (j in 0 until res.length()) {
                            val r = res.optJSONObject(j) ?: continue
                            col.addView(textView(
                                "Pass " + r.optInt("pass") + " — Found: " +
                                    r.optString("found") + " Done: " +
                                    r.optString("done"),
                                12.5f, SUB).apply {
                                setPadding(dp(40), dp(1), 0, dp(1))
                            })
                        }
                    }
                }
            }
            startPositionTicker()
        }

        // Placeholder (Thursday/Friday breaker) block.
        e.placeholder?.let { ph ->
            if (ph.optString("headline").isNotEmpty()) {
                col.addView(divider())
                col.addView(placeholderBlock(e, ph))
            }
        }

        // Description — the publish text for episodes; the body for
        // text-only entries. Content entries carry their text as the
        // script instead (mirrors the site).
        if (e.kind != "content" && e.description.isNotEmpty()) {
            col.addView(divider())
            col.addView(textView(
                if (e.mediaType == "text") "Text" else "Description", 13f, GOLD, bold = true))
            col.addView(textView(e.description, 15f, TEXT).apply { setPadding(0, dp(6), 0, 0) })
        }

        // Script — full text for content entries; the pre-approved
        // script rides along on Saturday episode entries.
        if (e.script.isNotEmpty()) {
            col.addView(divider())
            col.addView(textView(
                if (e.kind == "content") "Script" else "Pre-Approved Script",
                13f, GOLD, bold = true))
            col.addView(textView(e.script, 14f, TEXT).apply { setPadding(0, dp(6), 0, 0) })
        }

        if (e.sources.length() > 0) {
            col.addView(divider())
            col.addView(textView("Sources", 13f, GOLD, bold = true))
            for (i in 0 until e.sources.length()) {
                val s = e.sources.optJSONObject(i) ?: continue
                val name = s.optString("name").ifEmpty { s.optString("source") }
                val url = s.optString("url")
                if (name.isEmpty() && url.isEmpty()) continue
                col.addView(linkLine(name.ifEmpty { url }, url))
            }
        }

        if (e.artifacts.length() > 0) {
            col.addView(divider())
            col.addView(textView("Artifacts", 13f, GOLD, bold = true))
            for (i in 0 until e.artifacts.length()) {
                val a = e.artifacts.optJSONObject(i) ?: continue
                val item = a.optString("item")
                val source = a.optString("source")
                val url = a.optString("url")
                val label = when {
                    item.isNotEmpty() && source.isNotEmpty() -> "$item — $source"
                    item.isNotEmpty() -> item
                    else -> source
                }
                if (label.isEmpty() && url.isEmpty()) continue
                col.addView(linkLine(label.ifEmpty { url }, url))
            }
        }

        if (e.adjustment.isNotEmpty()) {
            col.addView(divider())
            col.addView(textView("Your adjustment note", 13f, GOLD, bold = true))
            col.addView(textView(e.adjustment, 15f, TEXT).apply { setPadding(0, dp(6), 0, 0) })
        }

        // Actions — the site's three buttons, in the site's order.
        col.addView(divider())
        val decided = e.status == "approved" || e.status == "pre-approved"
        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        val isContent = e.kind == "content"
        val adjBtn = actionButton(if (isContent) "Pre-Adjust" else "Adjust", primary = false, rounded = true)
        val editBtn = actionButton(if (isContent) "Pre-Edit" else "Edit", primary = false, rounded = true)
        val apprBtn = actionButton(if (isContent) "Pre-Approve" else "Approve", primary = true, rounded = true)
        for (b in listOf(adjBtn, editBtn, apprBtn)) {
            b.isEnabled = !decided
            row.addView(b, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                marginEnd = dp(8)
            })
        }
        adjBtn.setOnClickListener { showAdjustDialog(e) }
        editBtn.setOnClickListener { showEditor(e) }
        apprBtn.setOnClickListener { showApproveConfirm(e) }
        col.addView(row)

        val scroll = ScrollView(this).apply { addView(col) }
        val footer = detailFooter
        if (footer == null) {
            root.addView(scroll)
        } else {
            // Scrolling content above, the status line + Mark footer
            // pinned at the bottom of the screen.
            val outer = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
            }
            outer.addView(scroll, LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))
            outer.addView(footer, LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
            root.addView(outer)
        }
    }

    private fun markerRow(e: Entry, m: JSONObject, decided: Boolean): View {
        val pos = m.optDouble("position_secs", 0.0)
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(0, dp(4), 0, dp(4))
        }
        // Small square marker controls (Luddy, 2026-11-09): slim
        // time chip, pencil-icon Edit at 30dp, × close at 24dp —
        // the freed width goes to the note text.
        val timeBtn = actionButton(fmtDuration(pos), primary = false).apply {
            textSize = 12f
            minWidth = 0
            minHeight = 0
            setPadding(dp(7), dp(3), dp(7), dp(3))
            setOnClickListener {
                val c = controller ?: return@setOnClickListener
                // 10-second pre-roll (Luddy, 2026-11-09): by the
                // time he taps a marker he has just heard the issue,
                // so playback starts ten seconds before it.
                c.seekTo((((pos - 10.0).coerceAtLeast(0.0)) * 1000).toLong())
                c.play()
            }
        }
        row.addView(timeBtn, LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        val note = textView(m.optString("note"), 14f, TEXT).apply {
            setPadding(dp(8), 0, 0, 0)
        }
        row.addView(note, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        if (!decided) {
            // Note edit (Luddy, 2026-11-09): the note text can be
            // revised after the marker is dropped — the position
            // never moves.
            val edit = actionButton("✎", primary = false).apply {
                textSize = 15f
                minWidth = 0
                minHeight = 0
                setPadding(0, 0, 0, 0)
                setOnClickListener { showMarkerEditDialog(e, m) }
            }
            row.addView(edit, LinearLayout.LayoutParams(dp(30), dp(30)).apply {
                marginStart = dp(6)
            })
            val del = actionButton("✕", primary = false).apply {
                textSize = 12f
                minWidth = 0
                minHeight = 0
                setPadding(0, 0, 0, 0)
                setOnClickListener {
                    postAction("/approve/api/marker-delete",
                        JSONObject().put("slug", e.slug).put("id", m.optString("id")),
                        "Marker deletion")
                }
            }
            row.addView(del, LinearLayout.LayoutParams(dp(24), dp(24)).apply {
                marginStart = dp(6)
            })
        }
        return row
    }

    private fun linkLine(label: String, url: String): View {
        val tv = textView(label, 14f, if (url.startsWith("http")) GOLD else TEXT)
        tv.setPadding(0, dp(5), 0, 0)
        if (url.startsWith("http")) {
            tv.isClickable = true
            tv.setOnClickListener { startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))) }
        }
        return tv
    }

    private fun placeholderBlock(e: Entry, ph: JSONObject): View {
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(CARD)
            setPadding(dp(12), dp(10), dp(12), dp(10))
        }
        box.addView(textView("Worthy Thursday/Friday Story — Awaiting Your Decision",
            12f, GOLD, bold = true))
        box.addView(textView(ph.optString("headline"), 16f, TEXT, bold = true).apply {
            setPadding(0, dp(6), 0, 0)
        })
        val summary = ph.optString("summary")
        if (summary.isNotEmpty()) {
            box.addView(textView(summary, 14f, TEXT).apply { setPadding(0, dp(4), 0, 0) })
        }
        val srcUrl = ph.optString("source_url")
        val srcName = ph.optString("source_name")
        if (srcUrl.startsWith("http")) {
            box.addView(linkLine("Source: ${srcName.ifEmpty { "link" }}", srcUrl))
        }
        val decision = ph.optString("decision")
        if (decision == "include") {
            box.addView(textView(
                "Included" + if (ph.has("folded_at")) " — folded into the script" else "",
                14f, GOLD, bold = true).apply { setPadding(0, dp(8), 0, 0) })
        } else if (decision == "skip") {
            box.addView(textView("Left Out", 14f, SUB, bold = true).apply {
                setPadding(0, dp(8), 0, 0)
            })
        } else {
            val row = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                setPadding(0, dp(10), 0, 0)
            }
            val inc = actionButton("Include", primary = true)
            val skip = actionButton("Leave out", primary = false)
            inc.setOnClickListener {
                postAction("/approve/api/placeholder",
                    JSONObject().put("slug", e.slug).put("decision", "include"),
                    "Include decision")
            }
            skip.setOnClickListener {
                postAction("/approve/api/placeholder",
                    JSONObject().put("slug", e.slug).put("decision", "skip"),
                    "Leave-out decision")
            }
            row.addView(inc, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                marginEnd = dp(8)
            })
            row.addView(skip, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
            box.addView(row)
        }
        return box
    }

    // ------------------------------------------------------------ actions

    private fun showAdjustDialog(e: Entry) {
        val input = EditText(this).apply {
            hint = "What should be corrected?"
            setText(e.adjustment)
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE
            minLines = 3
        }
        val box = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        box.addView(input)
        // "Correct Markers" (Luddy, 2026-11-09): a button on the
        // LEFT of the note area that fills the note with exactly that
        // text when the markers on the entry are the problem set to
        // solve; the note stays editable and Send works as before.
        val cpRow = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        val cpBtn = actionButton("Correct Markers", primary = false)
        cpBtn.setOnClickListener {
            input.setText("Correct Markers")
            input.setSelection(input.text.length)
        }
        cpRow.addView(cpBtn, LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        box.addView(cpRow, LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = dp(10) })
        AlertDialog.Builder(this)
            .setTitle(if (e.kind == "content") "Pre-Adjust" else "Adjust")
            .setView(box)
            .setPositiveButton("Send") { _, _ ->
                val note = input.text.toString().trim()
                if (note.isEmpty()) { toast("Write the adjustment note first."); return@setPositiveButton }
                postAction("/approve/api/adjustment",
                    JSONObject().put("slug", e.slug).put("adjustment", note),
                    "Adjustment")
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun showMarkerDialog(e: Entry) {
        val posSecs = ((controller?.currentPosition ?: 0L) / 1000).toInt()
        val input = EditText(this).apply {
            hint = "Describe the issue at this point"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE
            minLines = 3
        }
        AlertDialog.Builder(this)
            .setTitle("Mark issue at ${fmtDuration(posSecs.toDouble())}")
            .setView(input)
            .setPositiveButton("Save marker") { _, _ ->
                val note = input.text.toString().trim()
                if (note.isEmpty()) { toast("Describe the issue first."); return@setPositiveButton }
                postAction("/approve/api/marker",
                    JSONObject().put("slug", e.slug)
                        .put("position_secs", posSecs)
                        .put("note", note),
                    "Marker")
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    // Marker note edit (Luddy, 2026-11-09): after dropping a
    // marker he may pin down the word he meant and want the note
    // to say it. Only the note text changes — the position never
    // moves. The Worker stamps edited_at on the marker, and the
    // actions watch re-hands that one marker to the parent once
    // with the updated text.
    private fun showMarkerEditDialog(e: Entry, m: JSONObject) {
        val posSecs = m.optDouble("position_secs", 0.0)
        val input = EditText(this).apply {
            hint = "Describe the issue at this point"
            setText(m.optString("note"))
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE
            minLines = 3
            setSelection(text.length)
        }
        AlertDialog.Builder(this)
            .setTitle("Edit marker at ${fmtDuration(posSecs)}")
            .setView(input)
            .setPositiveButton("Save") { _, _ ->
                val note = input.text.toString().trim()
                if (note.isEmpty()) { toast("Write the note first."); return@setPositiveButton }
                postAction("/approve/api/marker-edit",
                    JSONObject().put("slug", e.slug)
                        .put("id", m.optString("id"))
                        .put("note", note),
                    "Marker edit")
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun showApproveConfirm(e: Entry) {
        val isContent = e.kind == "content"
        AlertDialog.Builder(this)
            .setTitle(if (isContent) "Pre-Approve" else "Approve")
            .setMessage(
                if (isContent) "Pre-Approve this content for Saturday production?"
                else "Commence Publish Activity?")
            .setPositiveButton(if (isContent) "Pre-Approve" else "Approve") { _, _ ->
                postAction("/approve/api/approve",
                    JSONObject().put("slug", e.slug),
                    if (isContent) "Pre-approval" else "Approval")
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun showEditor(e: Entry) {
        stopPositionTicker()
        val isContent = e.kind == "content"
        val current = if (isContent) e.script else e.description
        detailSlug = e.slug
        root.removeAllViews()
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(16), dp(16), dp(16))
        }
        col.addView(textView(
            if (isContent) "Pre-Edit — Script" else "Edit — Description",
            20f, TEXT, bold = true))
        col.addView(textView("“${e.title}” — ${e.category}", 13f, SUB).apply {
            setPadding(0, dp(4), 0, 0)
        })
        val edit = EditText(this).apply {
            setText(current)
            setSelection(current.length)
            setTextColor(TEXT); setHintTextColor(SUB)
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE
            gravity = Gravity.TOP or Gravity.START
            minLines = 12
        }
        col.addView(edit, LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f).apply { topMargin = dp(12) })
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(0, dp(12), 0, 0)
        }
        val save = actionButton("Save", primary = true)
        val cancel = actionButton("Cancel", primary = false)
        save.setOnClickListener {
            val value = edit.text.toString()
            if (isContent) {
                if (value.isEmpty()) { toast("The script cannot be empty."); return@setOnClickListener }
                postAction("/approve/api/script",
                    JSONObject().put("slug", e.slug).put("script", value), "Script edit")
            } else {
                postAction("/approve/api/description",
                    JSONObject().put("slug", e.slug).put("description", value), "Description edit")
            }
        }
        cancel.setOnClickListener { showDetail(e.slug) }
        row.addView(save, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
            marginEnd = dp(8)
        })
        row.addView(cancel, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        col.addView(row)
        root.addView(col)
    }

    // ------------------------------------------------------------ self-update
    // The Daily Reports Player's two-door pattern (Luddy, 2026-11-05):
    // the ljm3.me door rides behind the saved dashboard sign-in and
    // goes first (the software.aniello.net request has come back with
    // garbled TLS on Luddy's network before); the public software site
    // is the fallback door. Every door's failure is recorded separately
    // so a failure report shows the real cause, never just the last one.

    private fun checkForUpdate(manual: Boolean) {
        if (!manual) {
            val now = System.currentTimeMillis()
            if (now - lastUpdateCheckMs < 60_000) return
            lastUpdateCheckMs = now
        }
        val doorErrors = StringBuilder()
        thread {
            try {
                val basic = Credentials.authHeader(this)
                val doors = listOf(
                    VERSION_URL_LJM3 to basic,
                    VERSION_URL_SOFTWARE to null)
                var fetched: JSONObject? = null
                var lastError: Exception? = null
                for ((door, auth) in doors) {
                    try {
                        val conn = URL(door).openConnection() as HttpURLConnection
                        conn.useCaches = false
                        conn.setRequestProperty("Cache-Control", "no-cache")
                        conn.setRequestProperty("User-Agent", "Approve/" + BuildConfig.VERSION_NAME)
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
                        Diag.record(this, "checkForUpdate door: $door", e)
                        doorErrors.append(door).append(" — ")
                            .append(e.javaClass.simpleName).append(": ")
                            .append(e.message ?: "no detail").append("\n")
                    }
                }
                val info = fetched ?: throw lastError ?: IllegalStateException("Update check failed.")
                val latest = info.optLong("versionCode", 0)
                val latestName = info.optString("versionName", "")
                val apk = info.optString("apk", "approve.apk")
                val notes = info.optString("notes", "")
                val installed = packageManager.getPackageInfo(packageName, 0).longVersionCode
                if (latest > installed) {
                    if (manual || latest != lastPromptedVersionCode) {
                        lastPromptedVersionCode = latest
                        post { promptUpdate(latestName, apk, notes) }
                    }
                } else if (manual) {
                    post { toast("No update available — you have the latest version.") }
                }
            } catch (e: Exception) {
                Diag.record(this, "checkForUpdate", e)
                if (manual) {
                    val detail = if (doorErrors.isNotEmpty()) doorErrors.toString().trim()
                        else "${e.javaClass.simpleName}: ${e.message ?: "no detail"}"
                    post { showUpdateFailure(detail) }
                }
            }
        }
    }

    private fun showUpdateFailure(detail: String) {
        AlertDialog.Builder(this)
            .setTitle("Update check failed")
            .setMessage(detail + "\n\nCopy this and send it to JMan, and he will fix the real cause.")
            .setPositiveButton("Copy") { _, _ ->
                val cm = getSystemService(android.content.ClipboardManager::class.java)
                cm?.setPrimaryClip(android.content.ClipData.newPlainText("Approve update check failure", detail))
                toast("Copied — paste it to JMan.")
            }
            .setNegativeButton("Close", null)
            .show()
    }

    private fun promptUpdate(versionName: String, apk: String, notes: String) {
        AlertDialog.Builder(this)
            .setTitle("Update available")
            .setMessage("Approve Player $versionName is available." +
                if (notes.isNotEmpty()) "\n\n$notes" else "")
            .setPositiveButton("Update") { _, _ -> downloadUpdate(apk) }
            .setNegativeButton("Later", null)
            .show()
    }

    private fun downloadUpdate(apk: String) {
        toast("Downloading update…")
        thread {
            val basic = Credentials.authHeader(this)
            val doors = listOf(
                "$BASE/approve/$apk" to basic,
                APK_BASE_SOFTWARE + apk to null)
            var lastError: Exception? = null
            var done = false
            for ((door, auth) in doors) {
                try {
                    val dir = File(cacheDir, "updates").apply { mkdirs() }
                    val out = File(dir, "update.apk")
                    val conn = URL(door).openConnection() as HttpURLConnection
                    conn.useCaches = false
                    conn.setRequestProperty("Cache-Control", "no-cache")
                    conn.setRequestProperty("User-Agent", "Approve/" + BuildConfig.VERSION_NAME)
                    if (auth != null) conn.setRequestProperty("Authorization", auth)
                    conn.connectTimeout = 15_000
                    conn.readTimeout = 60_000
                    if (conn.responseCode != 200) throw IllegalStateException("HTTP ${conn.responseCode}")
                    conn.inputStream.use { input -> out.outputStream().use { input.copyTo(it) } }
                    done = true
                    post { installUpdate(out) }
                    break
                } catch (e: Exception) {
                    lastError = e
                    Diag.record(this, "updateDownload door: $door", e)
                }
            }
            if (!done) {
                val detail = "${lastError?.javaClass?.simpleName ?: "Error"}: ${lastError?.message ?: "no detail"}"
                post { showUpdateFailure("The download failed on both routes. Last error: $detail") }
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
        // Auto-restart after the update, as in the player: arm the
        // UpdateRestartReceiver's marker BEFORE installing; the
        // install itself goes through the classic installer screen.
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
}
