package net.aniello.mediamanager

import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.RectF
import android.media.ExifInterface
import java.io.ByteArrayInputStream
import android.graphics.Color
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.DocumentsContract
import android.provider.OpenableColumns
import android.provider.Settings
import android.text.Editable
import android.text.TextUtils
import android.text.TextWatcher
import android.util.Base64
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.BaseAdapter
import android.widget.AutoCompleteTextView
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.Spinner
import android.widget.TextView
import android.widget.Toast
import androidx.core.content.FileProvider
import androidx.recyclerview.widget.ItemTouchHelper
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout
import android.graphics.drawable.GradientDrawable
import android.media.MediaMetadataRetriever
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.util.Collections
import java.util.concurrent.Executors
import kotlin.concurrent.thread

/**
 * LJM3 Media Manager — the phone companion to the ljm3.me media page.
 * Manages the ljm3-multimedia bucket through the dashboard-authenticated
 * media-admin API: browse photos, videos, and audio in their live order,
 * drag to reorder (the server renames the NNN key prefixes, so the
 * bucket stays the catalog), delete, and add new items from the phone,
 * a web link (Dropbox included), a network share (WebDAV relay), or
 * the guided export paths for Amazon Photos, Facebook and Instagram.
 * A fourth tab manages the links catalog (add, rename, move between
 * categories / subcategories, reorder, delete). Self-update rides
 * ljm3.me behind the sign-in first and software.aniello.net second,
 * like the other estate apps.
 */
class MainActivity : Activity() {

    companion object {
        private const val BASE = "https://ljm3.me"
        private const val VERSION_URL_LJM3 = "$BASE/mediamanager/app-version.json"
        private const val VERSION_URL_SOFTWARE = "https://software.aniello.net/apps/media-manager-version.json"
        private const val APK_BASE_SOFTWARE = "https://software.aniello.net/apps/"
        private const val REQ_GALLERY = 1001
        private const val REQ_FILES = 1002
        private const val NEW_CATEGORY_LABEL = "New category…"
        val BG: Int = Color.rgb(11, 15, 22)
        val CARD: Int = Color.rgb(20, 33, 47)
        val GOLD: Int = Color.rgb(201, 168, 106)
        val SUB: Int = Color.rgb(159, 179, 200)
        val TEXT: Int = Color.rgb(232, 237, 242)
    }

    private val ui = Handler(Looper.getMainLooper())
    private lateinit var root: FrameLayout
    private var currentType = "photos"
    private val lists = HashMap<String, MutableList<MediaItem>>()
    private val dirty = HashMap<String, Boolean>()
    private lateinit var adapter: MediaAdapter
    private lateinit var swipe: SwipeRefreshLayout
    private lateinit var statusLine: TextView
    private lateinit var optionsPanel: LinearLayout
    private lateinit var addPhotoBtn: Button
    private lateinit var addVideoBtn: Button
    private lateinit var addAudioBtn: Button
    private lateinit var addLinkBtn: Button
    private lateinit var renameBtn: Button
    private lateinit var moveLinkBtn: Button
    private lateinit var searchField: EditText
    private var selectedKey: String? = null
    // Multi-select MOVE state (v1.17): per media type, the KEYS of the
    // items whose row checkbox is ticked. Deliberately independent of
    // selectedKey (the single row-tap selection Rename acts on): ticking
    // checkboxes never changes the Rename selection, and tapping a row
    // never changes this set. The move itself is local only — it
    // reorders lists[type] and marks the tab dirty until Save Order.
    private val moveSelection = HashMap<String, MutableSet<String>>()
    // Per-tab search text, kept in memory while the app runs (v1.11).
    // Filtering only ever narrows what is DISPLAYED — the underlying
    // lists and their order are never touched by a search.
    private val searchQueries = HashMap<String, String>()
    private val statusByType = HashMap<String, String>()
    private lateinit var saveOrderBtn: Button
    private lateinit var tabPhotos: Button
    private lateinit var tabVideos: Button
    private lateinit var tabAudio: Button
    private lateinit var tabLinks: Button
    private lateinit var linksAdapter: LinksAdapter
    private lateinit var linksRv: RecyclerView
    private lateinit var linksTouchHelper: ItemTouchHelper
    // Links catalog state. There is NO predefined category list: the
    // catalog starts empty, and categories / subcategories exist only
    // because links were placed under them.
    private var linksLoaded = false
    private var linksCategories: List<String> = emptyList()
    private val linksItems = mutableListOf<Api.LinkItem>()
    // Links selection + unsaved local group orders (v1.11): tapping a
    // row selects it for the Rename / Move buttons, and ▲/▼ moves only
    // change the local order until Save Order posts each dirty group.
    private var selectedLinkId: String? = null
    // Multi-select MOVE state for links (v1.17): the ids whose row
    // checkbox is ticked, independent of selectedLinkId (the single
    // row-tap selection). While this set is non-empty the Move button
    // acts on the whole set; with it empty, Move Link keeps its exact
    // single-selection behavior. Bulk link moves are server-side
    // immediate (Api.linksMove per link) — no Save Order.
    private val linkMoveSelection = LinkedHashSet<String>()
    private val linksDirtyGroups = HashSet<Pair<String, String>>()
    // Set by the Links drag helper whenever a drag step actually
    // swapped two links; clearView turns it into the status line and
    // the lit Save Order button once the drag ends (v1.12).
    private var linksDragMoved = false
    private val faviconCache = HashMap<String, Bitmap>()
    private val faviconLoading = HashSet<String>()
    private val faviconPool = Executors.newFixedThreadPool(2)
    private var lastUpdateCheckMs = 0L
    private var lastPromptedVersionCode = 0L
    private var signedInOnce = false
    private var reorderRunning = false


    // ------------------------------------------------------------ photo orientation (v1.19)
    // The bucket stores phone photos exactly as captured, EXIF tag and
    // all. Browsers honor the EXIF orientation tag automatically;
    // BitmapFactory does not, so before v1.19 the app showed
    // EXIF-rotated photos sideways (Luddy's photo 002, from 1985).
    // These helpers read the tag with the framework ExifInterface and
    // bake it into the pixels wherever the app decodes a photo.

    /** EXIF orientation of stored image bytes; 0 when absent/unreadable. */
    private fun exifOrientationOf(bytes: ByteArray): Int {
        return try {
            ExifInterface(ByteArrayInputStream(bytes))
                .getAttributeInt(ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL)
        } catch (e: Exception) { 0 }
    }

    /** Returns bmp transformed per the EXIF orientation value (a new
     *  bitmap when a transform applies; bmp itself for 0/1/undefined). */
    private fun applyExifOrientation(bmp: Bitmap, orientation: Int): Bitmap {
        val m = Matrix()
        when (orientation) {
            ExifInterface.ORIENTATION_FLIP_HORIZONTAL -> m.postScale(-1f, 1f)
            ExifInterface.ORIENTATION_ROTATE_180 -> m.postRotate(180f)
            ExifInterface.ORIENTATION_FLIP_VERTICAL -> m.postScale(1f, -1f)
            ExifInterface.ORIENTATION_TRANSPOSE -> { m.postRotate(90f); m.postScale(-1f, 1f) }
            ExifInterface.ORIENTATION_ROTATE_90 -> m.postRotate(90f)
            ExifInterface.ORIENTATION_TRANSVERSE -> { m.postRotate(270f); m.postScale(-1f, 1f) }
            ExifInterface.ORIENTATION_ROTATE_270 -> m.postRotate(270f)
            else -> return bmp
        }
        val out = Bitmap.createBitmap(bmp, 0, 0, bmp.width, bmp.height, m, true)
        if (out !== bmp) bmp.recycle()
        return out
    }

    /** Decodes image bytes and bakes any EXIF orientation into the pixels. */
    private fun decodeOriented(bytes: ByteArray, sampleSize: Int): Bitmap? {
        val opts = BitmapFactory.Options().apply { inSampleSize = sampleSize }
        val raw = BitmapFactory.decodeByteArray(bytes, 0, bytes.size, opts) ?: return null
        return applyExifOrientation(raw, exifOrientationOf(bytes))
    }

    private fun rotateBitmap(bmp: Bitmap, degrees: Float): Bitmap {
        val m = Matrix(); m.postRotate(degrees)
        val out = Bitmap.createBitmap(bmp, 0, 0, bmp.width, bmp.height, m, true)
        if (out !== bmp) bmp.recycle()
        return out
    }

    private fun flipBitmap(bmp: Bitmap, horizontal: Boolean): Bitmap {
        val m = Matrix()
        if (horizontal) m.postScale(-1f, 1f) else m.postScale(1f, -1f)
        val out = Bitmap.createBitmap(bmp, 0, 0, bmp.width, bmp.height, m, true)
        if (out !== bmp) bmp.recycle()
        return out
    }

    /** True image format from magic bytes (the bucket holds two JPEGs
     *  wearing .png/.webp names, so the extension cannot be trusted). */
    private fun imageFormatOf(bytes: ByteArray): String {
        if (bytes.size >= 3 && bytes[0] == 0xFF.toByte() && bytes[1] == 0xD8.toByte()) return "jpeg"
        if (bytes.size >= 4 && bytes[0] == 0x89.toByte() && bytes[1] == 0x50.toByte() &&
            bytes[2] == 0x4E.toByte() && bytes[3] == 0x47.toByte()) return "png"
        if (bytes.size >= 3 && bytes[0] == 0x47.toByte() && bytes[1] == 0x49.toByte() &&
            bytes[2] == 0x46.toByte()) return "gif"
        return "jpeg"
    }

    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).toInt()
    private fun post(block: () -> Unit) { ui.post(block) }
    private fun toast(msg: String) { Toast.makeText(this, msg, Toast.LENGTH_LONG).show() }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Diag.install(this)
        root = FrameLayout(this).apply { setBackgroundColor(BG) }
        setContentView(root)
        if (Credentials.hasCredentials(this)) {
            signedInOnce = true
            showLibrary()
            checkForUpdate(manual = false)
        } else {
            showLogin(null)
        }
        Diag.phase(this, "ui built")
    }

    override fun onResume() {
        super.onResume()
        if (signedInOnce && Credentials.hasCredentials(this)) checkForUpdate(manual = false)
    }

    // ------------------------------------------------------------ shared bits

    private fun roundedBg(color: Int): GradientDrawable = GradientDrawable().apply {
        shape = GradientDrawable.RECTANGLE
        cornerRadius = dp(10).toFloat()
        setColor(color)
    }

    private fun selectedBg(): GradientDrawable = GradientDrawable().apply {
        shape = GradientDrawable.RECTANGLE
        cornerRadius = dp(10).toFloat()
        setColor(CARD)
        setStroke(dp(2), GOLD)
    }

    private fun goldButton(text: String): Button = Button(this).apply {
        this.text = text
        setTextColor(Color.rgb(11, 15, 22))
        background = roundedBg(GOLD)
        textSize = 14f
    }

    private fun darkButton(text: String): Button = Button(this).apply {
        this.text = text
        setTextColor(TEXT)
        background = roundedBg(CARD)
        textSize = 14f
    }

    // v1.13/v1.14: the Links tab shows FOUR action buttons in the
    // shared row (Add Link / Rename Link / Move Link / Save Order)
    // where the media tabs showed three, and at the stock 14sp size
    // with default button padding and min-width the labels wrapped
    // to two lines - so Links went compact: single line, 11.5sp,
    // tight horizontal padding, no min-width floor, and equal
    // layout weights (1/1/1/1 - Save Order no wider than the rest),
    // so every label fits on one line. v1.14 captured the stock
    // values and restored them on the media tabs (the restore in
    // PIXELS: feeding getTextSize()'s px back through the sp-based
    // setter was the v1.13 leak that blew the media buttons up by
    // the display's scaled-density factor).
    // v1.18 (Luddy, Sunday, November 15, 2026): "Need same text and
    // sizing as on links option - for buttons." The compact Links
    // styling now applies to the shared action row on EVERY tab;
    // the v1.14 capture/restore switching is retired, and switching
    // tabs never changes the buttons' size. The values are applied
    // exactly as the Links path always applied them: 11.5sp through
    // the textSize property (COMPLEX_UNIT_SP - never px through the
    // sp setter), horizontal padding dp(4), min-width floors 0,
    // single line, weights 1/1/1/1.
    private fun actionRowBtns() = listOf(addLinkBtn, renameBtn, moveLinkBtn, saveOrderBtn)

    private fun styleActionBtnCompact(b: Button) {
        b.maxLines = 1
        b.isSingleLine = true
        b.textSize = 11.5f
        b.setPadding(dp(4), b.paddingTop, dp(4), b.paddingBottom)
        b.minWidth = 0
        b.minimumWidth = 0
    }

    private fun setActionBtnWeight(b: Button, w: Float) {
        if (w < 0f) return
        val lp = b.layoutParams as? LinearLayout.LayoutParams ?: return
        if (lp.weight != w) {
            lp.weight = w
            b.layoutParams = lp
        }
    }

    private fun styleActionBtns() {
        for (b in actionRowBtns()) {
            styleActionBtnCompact(b)
            setActionBtnWeight(b, 1f)
        }
    }

    private fun field(hint: String, inputType: Int? = null): EditText = EditText(this).apply {
        this.hint = hint
        setTextColor(TEXT)
        setHintTextColor(SUB)
        textSize = 15f
        if (inputType != null) this.inputType = inputType
        setPadding(dp(10), dp(8), dp(10), dp(8))
    }

    private fun setStatus(s: String, type: String = currentType) {
        statusByType[type] = s
        if (type == currentType) statusLine.text = "Status: $s"
    }

    private fun showStatusForCurrentTab() {
        statusLine.text = "Status: " + (statusByType[currentType] ?: "")
    }

    private fun label(text: String, size: Float = 14f, color: Int = TEXT): TextView = TextView(this).apply {
        this.text = text
        textSize = size
        setTextColor(color)
    }

    /** Display kind for a media type: "Photo" / "Video" / "Audio". */
    private fun kindName(type: String): String = when (type) {
        "photos" -> "Photo"
        "videos" -> "Video"
        else -> "Audio"
    }

    /** Lowercase kind for sentences: "photo" / "video" / "audio file". */
    private fun kindLower(type: String): String = when (type) {
        "photos" -> "photo"
        "videos" -> "video"
        else -> "audio file"
    }

    private fun fmtSize(bytes: Long): String {
        if (bytes < 1024) return "$bytes B"
        val units = arrayOf("KB", "MB", "GB", "TB")
        var v = bytes.toDouble()
        var i = -1
        do { v /= 1024.0; i++ } while (v >= 1024 && i < units.size - 1)
        return String.format("%.1f %s", v, units[i])
    }

    // ------------------------------------------------------------ login

    private fun showLogin(error: String?) {
        root.removeAllViews()
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(28), dp(48), dp(28), dp(28))
        }
        val logo = ImageView(this).apply {
            setImageResource(R.drawable.ljm3_logo)
            layoutParams = LinearLayout.LayoutParams(dp(84), dp(84)).apply { gravity = Gravity.CENTER_HORIZONTAL }
        }
        col.addView(logo)
        col.addView(label("LJM3 Media Manager", 22f, GOLD).apply {
            gravity = Gravity.CENTER
            setPadding(0, dp(14), 0, dp(4))
        })
        col.addView(label("Manage the photos, videos, audio, and links on your ljm3.me page. Sign in with your dashboard username and password.", 13.5f, SUB).apply {
            gravity = Gravity.CENTER
            setPadding(0, 0, 0, dp(18))
        })
        val user = field("Username")
        val pass = field("Password", android.text.InputType.TYPE_CLASS_TEXT or android.text.InputType.TYPE_TEXT_VARIATION_PASSWORD)
        col.addView(user)
        col.addView(pass)
        val err = label(error ?: "", 13.5f, Color.rgb(255, 154, 138))
        col.addView(err)
        val btn = goldButton("Sign In")
        col.addView(btn, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(50)).apply { topMargin = dp(14) })
        btn.setOnClickListener {
            val u = user.text.toString().trim()
            val p = pass.text.toString()
            if (u.isEmpty() || p.isEmpty()) { err.text = "Enter both the username and the password."; return@setOnClickListener }
            btn.isEnabled = false
            err.text = "Signing in…"
            thread {
                try {
                    Credentials.save(this, u, p)
                    Api.list(this, "photos")
                    post { signedInOnce = true; showLibrary(); checkForUpdate(manual = false) }
                } catch (e: Exception) {
                    Credentials.clear(this)
                    Diag.record(this, "signIn", e)
                    post { showLogin("Sign-in failed — check the username and password and try again.") }
                }
            }
        }
        root.addView(col)
    }

    // ------------------------------------------------------------ library

    private fun showLibrary() {
        root.removeAllViews()
        val col = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }

        val header = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(14), dp(10), dp(14), dp(6))
        }
        header.addView(ImageView(this).apply {
            setImageResource(R.drawable.ljm3_logo)
            layoutParams = LinearLayout.LayoutParams(dp(36), dp(36))
        })
        val titleCol = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(10), 0, 0, 0)
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        }
        titleCol.addView(label("LJM3 Media Manager", 18f, GOLD))
        // Installed version, shown small right under the app title —
        // the Daily Reports Player / Approve Player format (Luddy,
        // 2026-11-13). Read from the package so it can never drift
        // from the build actually on the phone.
        val versionText = try {
            "Version:  " + (packageManager.getPackageInfo(packageName, 0).versionName ?: "")
        } catch (e: Exception) { "" }
        if (versionText.isNotBlank()) {
            titleCol.addView(label(versionText, 12f, SUB).apply {
                includeFontPadding = false
                layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = dp(1) }
            })
        }
        header.addView(titleCol)
        val optionsBtn = darkButton("Options").apply { textSize = 12f }
        optionsBtn.setOnClickListener {
            optionsPanel.visibility = if (optionsPanel.visibility == View.VISIBLE) View.GONE else View.VISIBLE
        }
        header.addView(optionsBtn)
        col.addView(header)

        optionsPanel = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(14), dp(2), dp(14), dp(6))
            visibility = View.GONE
        }
        optionsPanel.addView(label("User: ${Credentials.username(this) ?: ""}", 13f, SUB).apply {
            setPadding(dp(2), dp(4), 0, dp(6))
        })
        val logOutBtn = darkButton("Log Out").apply { textSize = 13f }
        logOutBtn.setOnClickListener {
            optionsPanel.visibility = View.GONE
            AlertDialog.Builder(this)
                .setTitle("Log out?")
                .setMessage("Your sign-in will be removed from this phone. The library itself is not changed.")
                .setPositiveButton("Log Out") { _, _ ->
                    Credentials.clear(this)
                    showLogin(null)
                }
                .setNegativeButton("Cancel", null)
                .show()
        }
        optionsPanel.addView(logOutBtn, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(40)).apply { bottomMargin = dp(6) })
        val refreshBtn = darkButton("Refresh").apply { textSize = 13f }
        refreshBtn.setOnClickListener {
            optionsPanel.visibility = View.GONE
            if (currentType == "links") refreshLinks() else refresh(currentType, keepOrder = false)
        }
        optionsPanel.addView(refreshBtn, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(40)).apply { bottomMargin = dp(6) })
        val updateBtn = darkButton("Check For Updates").apply { textSize = 13f }
        updateBtn.setOnClickListener {
            optionsPanel.visibility = View.GONE
            checkForUpdate(manual = true)
        }
        optionsPanel.addView(updateBtn, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(40)))
        col.addView(optionsPanel)

        val tabs = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(dp(14), dp(4), dp(14), dp(4))
        }
        tabPhotos = darkButton("Photos").apply { textSize = 13f }
        tabVideos = darkButton("Videos").apply { textSize = 13f }
        tabAudio = darkButton("Audio").apply { textSize = 13f }
        tabLinks = darkButton("Links").apply { textSize = 13f }
        tabPhotos.setOnClickListener { switchTab("photos") }
        tabVideos.setOnClickListener { switchTab("videos") }
        tabAudio.setOnClickListener { switchTab("audio") }
        tabLinks.setOnClickListener { switchTab("links") }
        tabs.addView(tabPhotos, LinearLayout.LayoutParams(0, dp(42), 1f).apply { marginEnd = dp(6) })
        tabs.addView(tabVideos, LinearLayout.LayoutParams(0, dp(42), 1f).apply { marginEnd = dp(6) })
        tabs.addView(tabAudio, LinearLayout.LayoutParams(0, dp(42), 1f).apply { marginEnd = dp(6) })
        tabs.addView(tabLinks, LinearLayout.LayoutParams(0, dp(42), 1f))
        col.addView(tabs)

        val actions = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(dp(14), dp(4), dp(14), dp(4))
        }
        addPhotoBtn = darkButton("Add Photo")
        addVideoBtn = darkButton("Add Video")
        addAudioBtn = darkButton("Add Audio")
        addLinkBtn = goldButton("Add Link")
        renameBtn = darkButton("Rename Photo")
        moveLinkBtn = darkButton("Move Link")
        saveOrderBtn = goldButton("Save Order")
        addPhotoBtn.setOnClickListener { showSourceSheet("photos") }
        addVideoBtn.setOnClickListener { showSourceSheet("videos") }
        addAudioBtn.setOnClickListener { showSourceSheet("audio") }
        addLinkBtn.setOnClickListener { showAddLinkDialog() }
        renameBtn.setOnClickListener {
            if (currentType == "links") {
                selectedLink()?.let { showRenameLinkDialog(it) }
            } else if (currentType == "photos") showEditPhoto()
            else showRename()
        }
        moveLinkBtn.setOnClickListener {
            when {
                // Links, checkboxes ticked: bulk move of the whole set.
                currentType == "links" && linkMoveSelection.isNotEmpty() -> showBulkMoveLinksDialog()
                // Links, no checkboxes: today's single-selection move.
                currentType == "links" -> selectedLink()?.let { showMoveLinkDialog(it) }
                // Media tabs: move the checkbox-selected block locally.
                else -> showMoveSelectedMediaDialog()
            }
        }
        saveOrderBtn.setOnClickListener { if (currentType == "links") saveLinksOrder() else saveOrder() }
        actions.addView(addPhotoBtn, LinearLayout.LayoutParams(0, dp(42), 1f).apply { marginEnd = dp(6) })
        actions.addView(addVideoBtn, LinearLayout.LayoutParams(0, dp(42), 1f).apply { marginEnd = dp(6) })
        actions.addView(addAudioBtn, LinearLayout.LayoutParams(0, dp(42), 1f).apply { marginEnd = dp(6) })
        actions.addView(addLinkBtn, LinearLayout.LayoutParams(0, dp(42), 1f).apply { marginEnd = dp(6) })
        actions.addView(renameBtn, LinearLayout.LayoutParams(0, dp(42), 1f).apply { marginEnd = dp(6) })
        actions.addView(moveLinkBtn, LinearLayout.LayoutParams(0, dp(42), 1f).apply { marginEnd = dp(6) })
        actions.addView(saveOrderBtn, LinearLayout.LayoutParams(0, dp(42), 1.2f))
        col.addView(actions)

        // Search (v1.11): one field above the list on every tab, in
        // the Update page's format. Each tab keeps its own text; the
        // watcher only stores the text and re-filters the display.
        val searchRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(dp(14), 0, dp(14), dp(4))
        }
        searchField = field("Search")
        searchField.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) { }
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) { }
            override fun afterTextChanged(s: Editable?) {
                val q = s?.toString() ?: ""
                if (searchQueries[currentType] == q) return
                searchQueries[currentType] = q
                if (currentType == "links") renderLinks() else adapter.notifyDataSetChanged()
            }
        })
        searchRow.addView(searchField, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(44)))
        col.addView(searchRow)

        adapter = MediaAdapter()
        val rv = RecyclerView(this).apply {
            layoutManager = LinearLayoutManager(this@MainActivity)
            this.adapter = this@MainActivity.adapter
        }
        val touchHelper = ItemTouchHelper(object : ItemTouchHelper.SimpleCallback(ItemTouchHelper.UP or ItemTouchHelper.DOWN, 0) {
            override fun onMove(rv: RecyclerView, vh: RecyclerView.ViewHolder, target: RecyclerView.ViewHolder): Boolean {
                if (reorderRunning) return false
                val from = vh.bindingAdapterPosition
                val to = target.bindingAdapterPosition
                val items = lists[currentType] ?: return false
                // Adapter positions are positions in the (possibly
                // search-filtered) display; translate them to full-list
                // indices so a drag swaps the right two items in the
                // underlying order (v1.11).
                val vis = visibleMediaItems()
                if (from < 0 || to < 0 || from >= vis.size || to >= vis.size) return false
                val fi = vis[from].first
                val ti = vis[to].first
                if (fi !in items.indices || ti !in items.indices) return false
                if (fi != ti) {
                    Collections.swap(items, fi, ti)
                    dirty[currentType] = true
                    updateSaveOrder()
                }
                adapter.notifyItemMoved(from, to)
                return true
            }
            override fun clearView(rv: RecyclerView, vh: RecyclerView.ViewHolder) {
                super.clearView(rv, vh)
                adapter.refreshPositions()
            }
            override fun onSwiped(vh: RecyclerView.ViewHolder, dir: Int) { }
            override fun isLongPressDragEnabled(): Boolean = false
        })
        touchHelper.attachToRecyclerView(rv)
        adapter.touchHelper = touchHelper

        swipe = SwipeRefreshLayout(this).apply {
            setOnRefreshListener { refresh(currentType, keepOrder = false) }
            addView(rv)
        }
        col.addView(swipe, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))

        // The Links tab's list (v1.12): a RecyclerView like the media
        // tabs', shown instead of the media RecyclerView while Links
        // is active. Group headers are plain rows in the same list;
        // link rows carry a ≡ drag handle at the left edge, exactly
        // like the Update page. Dragging is confined to the dragged
        // link's own category/subcategory group (a move across a
        // header or into another group is refused — changing groups
        // stays the Move Link dialog's job), and it is disabled while
        // a Links search is filtering the display.
        linksAdapter = LinksAdapter()
        linksRv = RecyclerView(this).apply {
            layoutManager = LinearLayoutManager(this@MainActivity)
            this.adapter = this@MainActivity.linksAdapter
            visibility = View.GONE
            setPadding(dp(14), dp(4), dp(14), dp(10))
            clipToPadding = false
        }
        linksTouchHelper = ItemTouchHelper(object : ItemTouchHelper.SimpleCallback(ItemTouchHelper.UP or ItemTouchHelper.DOWN, 0) {
            override fun getMovementFlags(rv: RecyclerView, vh: RecyclerView.ViewHolder): Int {
                val q = (searchQueries["links"] ?: "").trim()
                val draggable = vh is LinksAdapter.EntryHolder && q.isEmpty()
                val dragFlags = if (draggable) ItemTouchHelper.UP or ItemTouchHelper.DOWN else 0
                return makeMovementFlags(dragFlags, 0)
            }
            override fun onMove(rv: RecyclerView, vh: RecyclerView.ViewHolder, target: RecyclerView.ViewHolder): Boolean {
                val from = vh.bindingAdapterPosition
                val to = target.bindingAdapterPosition
                val rows = linksAdapter.rows
                if (from !in rows.indices || to !in rows.indices) return false
                val src = rows[from] as? LinkRow.Entry ?: return false
                val dst = rows[to] as? LinkRow.Entry ?: return false
                // Group constraint: the dragged link may only trade
                // places with a link in its own group — never across
                // a group header or into another group.
                if (src.item.cat != dst.item.cat || src.item.sub != dst.item.sub) return false
                swapLinksForDrag(src.item, dst.item)
                // Keep the display rows in step in place (a rebuild
                // mid-drag would fight the helper's bookkeeping); the
                // two entries trade their display places along with
                // their catalog places.
                rows[from] = dst.copy(displayIdx = src.displayIdx)
                rows[to] = src.copy(displayIdx = dst.displayIdx)
                linksAdapter.notifyItemMoved(from, to)
                return true
            }
            override fun clearView(rv: RecyclerView, vh: RecyclerView.ViewHolder) {
                super.clearView(rv, vh)
                // A full rebind settles the "N of M" sub-lines, and a
                // drag that changed anything leaves the same trail as
                // an ▲/▼ move: group dirty, Save Order lit, status set.
                linksAdapter.notifyDataSetChanged()
                if (linksDragMoved) {
                    linksDragMoved = false
                    updateSaveOrder()
                    setStatus("Order changed — tap Save Order to save it.", "links")
                }
            }
            override fun onSwiped(vh: RecyclerView.ViewHolder, dir: Int) { }
            override fun isLongPressDragEnabled(): Boolean = false
        })
        linksTouchHelper.attachToRecyclerView(linksRv)
        linksAdapter.touchHelper = linksTouchHelper
        col.addView(linksRv, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))

        val footer = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(dp(14), dp(6), dp(14), dp(12))
        }
        statusLine = label("Status: ", 12.5f, SUB)
        footer.addView(statusLine)
        col.addView(footer)

        root.addView(col)
        updateTabs()
        updateSaveOrder()
        if (lists["photos"] == null) refresh("photos", keepOrder = false)
        if (lists["videos"] == null) refresh("videos", keepOrder = false)
        if (lists["audio"] == null) refresh("audio", keepOrder = false)
    }

    private fun switchTab(type: String) {
        if (currentType == type) return
        currentType = type
        selectedKey = null
        selectedLinkId = null
        // The search field shows this tab's own remembered text; the
        // watcher stores it back under the new tab and re-filters.
        searchField.setText(searchQueries[type] ?: "")
        updateTabs()
        adapter.notifyDataSetChanged()
        updateSaveOrder()
        if (type == "links") {
            if (linksLoaded) renderLinks() else refreshLinks()
        }
    }

    /**
     * The media rows currently on display for the current tab, as
     * (full-list index, item) pairs with the tab's search applied
     * (title contains, case-insensitive). With no search text this is
     * the whole list in order. Filtering never mutates the list.
     */
    private fun visibleMediaItems(): List<Pair<Int, MediaItem>> {
        val items = lists[currentType] ?: return emptyList()
        val q = (searchQueries[currentType] ?: "").trim().lowercase()
        val indexed = items.mapIndexed { i, it -> i to it }
        if (q.isEmpty()) return indexed
        return indexed.filter { it.second.title.lowercase().contains(q) }
    }

    private fun updateTabs() {
        val on = GOLD; val off = CARD
        tabPhotos.background = roundedBg(if (currentType == "photos") on else off)
        tabPhotos.setTextColor(if (currentType == "photos") Color.rgb(11, 15, 22) else TEXT)
        tabVideos.background = roundedBg(if (currentType == "videos") on else off)
        tabVideos.setTextColor(if (currentType == "videos") Color.rgb(11, 15, 22) else TEXT)
        tabAudio.background = roundedBg(if (currentType == "audio") on else off)
        tabAudio.setTextColor(if (currentType == "audio") Color.rgb(11, 15, 22) else TEXT)
        tabLinks.background = roundedBg(if (currentType == "links") on else off)
        tabLinks.setTextColor(if (currentType == "links") Color.rgb(11, 15, 22) else TEXT)
        val isLinks = currentType == "links"
        addPhotoBtn.visibility = if (currentType == "photos") View.VISIBLE else View.GONE
        addVideoBtn.visibility = if (currentType == "videos") View.VISIBLE else View.GONE
        addAudioBtn.visibility = if (currentType == "audio") View.VISIBLE else View.GONE
        addLinkBtn.visibility = if (isLinks) View.VISIBLE else View.GONE
        // v1.17: the Move button now shows on EVERY tab — on media
        // tabs it moves the checkbox-selected block, on Links it moves
        // the checkbox set (or the single selection, as before).
        moveLinkBtn.visibility = View.VISIBLE
        swipe.visibility = if (isLinks) View.GONE else View.VISIBLE
        linksRv.visibility = if (isLinks) View.VISIBLE else View.GONE
        renameBtn.text = if (isLinks) "Rename Link" else if (currentType == "photos") "Edit Photo" else "Rename ${kindName(currentType)}"
        styleActionBtns()
        searchField.hint = if (isLinks) "Search links by name, address, or category…"
            else "Search ${currentType} by name…"
        updateRename()
        showStatusForCurrentTab()
    }

    private fun updateRename() {
        // Rename acts on the single selected row and stays disabled
        // until there is one — the Update page's rule. The Move
        // button's state is its own rule now (v1.17): see
        // updateMoveButton.
        val has = if (currentType == "links") selectedLinkId != null else selectedKey != null
        renameBtn.isEnabled = has
        renameBtn.alpha = if (has) 1f else 0.45f
        updateMoveButton()
    }

    /**
     * The Move button's label and enabled state (v1.17), refreshed
     * whenever a checkbox set changes, a selection changes, or the
     * tab switches (updateRename and updateTabs both funnel here).
     * Media tabs: "Move Selected (N)", enabled only while N >= 1 for
     * the CURRENT tab's set (each tab keeps its own set). Links tab:
     * while any link checkbox is ticked, "Move Selected (N)" acting
     * on the set; with none ticked, "Move Link" with the exact old
     * single-selection enable rule.
     */
    private fun updateMoveButton() {
        if (!::moveLinkBtn.isInitialized) return
        if (currentType == "links") {
            val n = linkMoveSelection.size
            if (n >= 1) {
                moveLinkBtn.text = "Move Selected ($n)"
                moveLinkBtn.isEnabled = true
                moveLinkBtn.alpha = 1f
            } else {
                moveLinkBtn.text = "Move Link"
                val has = selectedLinkId != null
                moveLinkBtn.isEnabled = has
                moveLinkBtn.alpha = if (has) 1f else 0.45f
            }
        } else {
            val n = moveSelection[currentType]?.size ?: 0
            moveLinkBtn.text = "Move Selected ($n)"
            moveLinkBtn.isEnabled = n >= 1
            moveLinkBtn.alpha = if (n >= 1) 1f else 0.45f
        }
    }

    private fun updateSaveOrder() {
        val d = if (currentType == "links") linksDirtyGroups.isNotEmpty() else dirty[currentType] == true
        saveOrderBtn.isEnabled = d
        saveOrderBtn.alpha = if (d) 1f else 0.45f
    }

    private fun selectedLink(): Api.LinkItem? = linksItems.firstOrNull { it.id == selectedLinkId }

    private fun refresh(type: String, keepOrder: Boolean) {
        if (type == currentType) swipe.isRefreshing = true
        setStatus("Loading $type…", type)
        thread {
            try {
                val items = Api.list(this, type)
                post {
                    if (!(keepOrder && dirty[type] == true)) {
                        lists[type] = items.toMutableList()
                        dirty[type] = false
                        // A reload replaces the list wholesale (v1.17):
                        // drop move-checkbox keys that no longer exist
                        // so a stale key can never inflate the count.
                        moveSelection[type]?.retainAll(items.map { it.key }.toSet())
                        if (type == currentType) updateMoveButton()
                    }
                    // The finished status is recorded for the tab whether
                    // or not it is on screen (Luddy, 2026-11-14): both tabs
                    // load at startup, and a tab whose load finished in the
                    // background used to sit on "Loading..." forever when
                    // switched to. setStatus already paints only the
                    // visible tab; storing must be unconditional.
                    setStatus("${lists[type]?.size ?: 0} $type, in the order they appear on the page. Drag the ≡ handle to reorder, then Save Order.", type)
                    if (type == currentType) {
                        adapter.notifyDataSetChanged()
                        updateSaveOrder()
                        swipe.isRefreshing = false
                    }
                }
            } catch (e: Exception) {
                Diag.record(this, "refresh $type", e)
                post {
                    setStatus("Could not load $type: ${e.message}", type)
                    if (type == currentType) {
                        swipe.isRefreshing = false
                    }
                }
            }
        }
    }

    /**
     * Step-wise reorder (v1.1). The v1.0 bulk /media-admin/reorder copied
     * every changed item TWICE inside one request; on multi-GB videos the
     * request died partway and stranded an item in a temp key (2026-11-12,
     * videos/007 — recovered server-side). Now Save Order plans the whole
     * permutation up front and executes it as single-item renames:
     * direct moves whose target key is free, plus exactly one parked item
     * per permutation cycle, parked at inbox/mm-reorder-<yyyyMMddHHmmss>/
     * <n><ext> (the rename endpoint permits that reserved prefix, and a
     * video's poster follows it server-side). On any step failure the
     * parked item is restored best-effort — original key first, its final
     * key if that is taken — and the list reloads from the server: a
     * partly applied order is acceptable, a lost item is not.
     */
    private data class ReorderStep(val itemIdx: Int, val from: String, val to: String, val park: Boolean)

    private fun finalKeyFor(type: String, position: Int, currentKey: String): String {
        val base = currentKey.substringAfterLast('/')
        val dot = base.lastIndexOf('.')
        val ext = base.substring(dot)
        val stem = base.substring(0, dot).replace(Regex("^\\d+\\s*-\\s*"), "")
        return type + "/" + String.format(java.util.Locale.US, "%03d", position + 1) + " - " + stem + ext
    }

    private fun saveOrder() {
        if (reorderRunning) return
        val type = currentType
        val localItems = lists[type]?.toList() ?: return
        if (localItems.isEmpty()) return
        // Stale-list hardening (v1.17, parity with the Update page's
        // reconcileOrderItems): the library may have changed since
        // this tab's list loaded, so re-fetch it on a background
        // thread BEFORE planning any rename. The reconciled list —
        // local order kept for items that still exist (using the
        // FRESH item objects), removed items dropped, added items
        // appended at the end in server order — replaces lists[type]
        // and becomes the plan's input. If the fetch fails, the local
        // list is planned unchanged, exactly as before. Setting
        // reorderRunning here also blocks drags during the fetch, so
        // the local order cannot shift under the reconcile.
        reorderRunning = true
        if (::swipe.isInitialized) swipe.isEnabled = false
        thread {
            val fresh = try { Api.list(this, type) } catch (e: Exception) { null }
            post {
                var note = ""
                var items = localItems
                if (fresh != null) {
                    val freshByKey = fresh.associateBy { it.key }
                    val localKeys = localItems.map { it.key }.toSet()
                    val kept = localItems.mapNotNull { freshByKey[it.key] }
                    val addedItems = fresh.filter { it.key !in localKeys }
                    val added = addedItems.size
                    val dropped = localItems.size - kept.size
                    val reconciled = kept + addedItems
                    lists[type] = reconciled.toMutableList()
                    moveSelection[type]?.retainAll(reconciled.map { it.key }.toSet())
                    if (type == currentType) {
                        adapter.notifyDataSetChanged()
                        updateMoveButton()
                    }
                    if (added > 0 || dropped > 0) {
                        val clauses = ArrayList<String>()
                        if (added > 0) clauses.add("$added item${if (added == 1) "" else "s"} added at the end")
                        if (dropped > 0) clauses.add("$dropped item${if (dropped == 1) "" else "s"} removed")
                        note = " The library had changed since this list loaded \u2014 " +
                            clauses.joinToString(" / ") +
                            " \u2014 so the order was matched to the current library first."
                    }
                    items = reconciled
                }
                if (items.isEmpty()) {
                    // Everything local was removed server-side and
                    // nothing replaced it: nothing left to save.
                    reorderRunning = false
                    swipe.isEnabled = true
                    dirty[type] = false
                    updateSaveOrder()
                    setStatus("That is already the saved order." + note, type)
                    return@post
                }
                executeSaveOrder(type, items, note)
            }
        }
    }

    /**
     * The original saveOrder body (planning + step-wise execution),
     * unchanged except for its inputs: [items] is the (possibly
     * reconciled) list to plan from, and [reconcileNote] is appended
     * to the success status when the reconcile changed anything.
     * Called with reorderRunning already set by saveOrder; every exit
     * path here clears it, as the old synchronous entry did by
     * setting it only once planning succeeded.
     */
    private fun executeSaveOrder(type: String, items: List<MediaItem>, reconcileNote: String) {
        val targets = items.mapIndexed { i, it -> finalKeyFor(type, i, it.key) }

        // Pure simulation: build the full ordered step list first.
        val cur = items.map { it.key }.toMutableList()
        val occupied = items.map { it.key }.toMutableSet()
        val pending = (0 until items.size).filter { cur[it] != targets[it] }.toMutableSet()
        if (pending.isEmpty()) {
            reorderRunning = false
            if (::swipe.isInitialized) swipe.isEnabled = true
            dirty[type] = false
            updateSaveOrder()
            setStatus("That is already the saved order." + reconcileNote, type)
            return
        }
        val stamp = java.text.SimpleDateFormat("yyyyMMddHHmmss", java.util.Locale.US).format(java.util.Date())
        val plan = ArrayList<ReorderStep>()
        var parkN = 0
        while (pending.isNotEmpty()) {
            val ready = pending.firstOrNull { targets[it] !in occupied }
            if (ready != null) {
                plan.add(ReorderStep(ready, cur[ready], targets[ready], false))
                occupied.remove(cur[ready]); occupied.add(targets[ready])
                cur[ready] = targets[ready]; pending.remove(ready)
            } else {
                val idx = pending.first()
                val ext = cur[idx].substring(cur[idx].lastIndexOf('.'))
                val tmp = "inbox/mm-reorder-$stamp/${parkN++}$ext"
                plan.add(ReorderStep(idx, cur[idx], tmp, true))
                occupied.remove(cur[idx]); occupied.add(tmp)
                cur[idx] = tmp
            }
        }

        reorderRunning = true
        saveOrderBtn.isEnabled = false
        swipe.isEnabled = false
        val progressText = label("Moving item 1 of ${plan.size} — do not close the app.", 14f, TEXT).apply {
            setPadding(dp(24), dp(20), dp(24), dp(8))
        }
        val dialog = AlertDialog.Builder(this)
            .setTitle("Saving the new order")
            .setView(progressText)
            .setCancelable(false)
            .create()
        dialog.show()
        setStatus("Saving the new order…", type)
        thread {
            var parkedIdx = -1
            var parkedTemp = ""
            var parkedOriginal = ""
            var failure: Exception? = null
            var failedItem = ""
            try {
                for ((n, step) in plan.withIndex()) {
                    post { progressText.text = "Moving item ${n + 1} of ${plan.size} — do not close the app." }
                    try {
                        Api.rename(this, step.from, step.to)
                    } catch (e: Exception) {
                        failedItem = items[step.itemIdx].title
                        throw e
                    }
                    if (step.park) {
                        parkedIdx = step.itemIdx; parkedTemp = step.to; parkedOriginal = step.from
                    } else if (step.itemIdx == parkedIdx) {
                        parkedIdx = -1; parkedTemp = ""; parkedOriginal = ""
                    }
                }
            } catch (e: Exception) {
                failure = e
                if (parkedIdx >= 0) {
                    try { Api.rename(this, parkedTemp, parkedOriginal) }
                    catch (e2: Exception) {
                        try { Api.rename(this, parkedTemp, targets[parkedIdx]) } catch (ignored: Exception) { }
                    }
                }
            }
            val err = failure
            post {
                dialog.dismiss()
                reorderRunning = false
                swipe.isEnabled = true
                if (err == null) {
                    dirty[type] = false
                    setStatus("Order saved — the page now shows this order." + reconcileNote, type)
                    toast("Order saved.")
                } else {
                    Diag.record(this, "saveOrder stepwise", err)
                    setStatus("Saving the order stopped at “$failedItem”: ${err.message} The list has been reloaded from the server — no item was lost, but the order may be partly applied.", type)
                    toast("Saving the order stopped — the list was reloaded.")
                }
                refresh(type, keepOrder = false)
            }
        }
    }

    private fun confirmDelete(item: MediaItem) {
        AlertDialog.Builder(this)
            .setTitle("Delete this ${kindLower(currentType)}?")
            .setMessage("“${item.title}” will be removed from the bucket and from the page. This cannot be undone from the app.")
            .setPositiveButton("Delete") { _, _ ->
                setStatus("Deleting…")
                thread {
                    try {
                        Api.delete(this, item.key)
                        post { refresh(currentType, keepOrder = dirty[currentType] == true) }
                    } catch (e: Exception) {
                        Diag.record(this, "delete", e)
                        post { setStatus("Delete failed: ${e.message}") }
                    }
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    // ------------------------------------------------------------ edit photo (v1.19)
    // Edit Photo replaces Rename Photo on the Photos tab (Luddy,
    // Monday, November 16, 2026): one dialog holding Rename,
    // Orientation (rotate / flip), and Crop. The editor starts from
    // the EXIF-applied image, so a stored-sideways photo opens
    // upright. Saving with changed pixels first parks the untouched
    // original at inbox/originals/<filename>, then writes the
    // re-encoded image (full resolution, JPEG quality 90, PNG kept
    // PNG, no EXIF orientation tag) back under the SAME key, so the
    // photo keeps its number, title, year, and place in the order;
    // a title change then rides the ordinary rename route, exactly
    // like the old Rename Photo. A title-only save never touches the
    // pixels and makes no backup.

    /** The crop surface: the photo fitted whole, a gold crop box over
     *  it that can be dragged to move, dragged by a corner to resize,
     *  or drawn fresh by dragging outside the box. Crop coordinates
     *  are image fractions (0..1); an optional pixel aspect (w/h)
     *  constrains resizes and fresh draws. */
    inner class PhotoCropView : View(this@MainActivity) {
        var bmp: Bitmap? = null
        var cropL = 0f; var cropT = 0f; var cropR = 1f; var cropB = 1f
        var aspect: Float? = null
        private var mode = 0 // 0 idle, 1 move, 2 resize corner, 3 draw new
        private var corner = -1 // 0 TL, 1 TR, 2 BR, 3 BL (the corner being dragged)
        private var grabDX = 0f; private var grabDY = 0f
        private var anchorX = 0f; private var anchorY = 0f
        private val dimPaint = Paint().apply { color = Color.argb(150, 3, 6, 10) }
        private val linePaint = Paint().apply { color = GOLD; style = Paint.Style.STROKE; strokeWidth = dp(2).toFloat() }
        private val handlePaint = Paint().apply { color = GOLD; style = Paint.Style.FILL }

        fun cropIsFull(): Boolean = cropL <= 0.001f && cropT <= 0.001f && cropR >= 0.999f && cropB >= 0.999f

        fun resetCrop() { cropL = 0f; cropT = 0f; cropR = 1f; cropB = 1f; invalidate() }

        /** Largest centered crop with the given pixel aspect (null = free). */
        fun applyAspect(a: Float?) {
            aspect = a
            val b = bmp ?: return
            if (a == null) { invalidate(); return }
            var fw = 1f
            var fh = fw * b.width / (a * b.height)
            if (fh > 1f) { fh = 1f; fw = fh * a * b.height / b.width }
            val cx = (cropL + cropR) / 2f
            val cy = (cropT + cropB) / 2f
            cropL = (cx - fw / 2f).coerceIn(0f, 1f - fw); cropR = cropL + fw
            cropT = (cy - fh / 2f).coerceIn(0f, 1f - fh); cropB = cropT + fh
            invalidate()
        }

        private fun imgRect(): RectF {
            val b = bmp ?: return RectF(0f, 0f, width.toFloat(), height.toFloat())
            val s = minOf(width.toFloat() / b.width, height.toFloat() / b.height)
            val w = b.width * s
            val h = b.height * s
            return RectF((width - w) / 2f, (height - h) / 2f, (width + w) / 2f, (height + h) / 2f)
        }

        override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
            val w = MeasureSpec.getSize(widthMeasureSpec)
            val b = bmp
            var h = if (b != null && b.width > 0) w * b.height / b.width else w
            h = h.coerceIn(dp(140), dp(520))
            setMeasuredDimension(w, h)
        }

        override fun onDraw(canvas: Canvas) {
            super.onDraw(canvas)
            val b = bmp ?: return
            val r = imgRect()
            canvas.drawBitmap(b, null, r, null)
            val cl = r.left + cropL * r.width()
            val ct = r.top + cropT * r.height()
            val cr = r.left + cropR * r.width()
            val cb = r.top + cropB * r.height()
            canvas.drawRect(r.left, r.top, r.right, ct, dimPaint)
            canvas.drawRect(r.left, cb, r.right, r.bottom, dimPaint)
            canvas.drawRect(r.left, ct, cl, cb, dimPaint)
            canvas.drawRect(cr, ct, r.right, cb, dimPaint)
            canvas.drawRect(cl, ct, cr, cb, linePaint)
            val hs = dp(9).toFloat()
            for ((hx, hy) in listOf(cl to ct, cr to ct, cr to cb, cl to cb))
                canvas.drawRect(hx - hs / 2, hy - hs / 2, hx + hs / 2, hy + hs / 2, handlePaint)
        }

        private fun toFrac(ev: MotionEvent): Pair<Float, Float> {
            val r = imgRect()
            val fx = ((ev.x - r.left) / r.width()).coerceIn(0f, 1f)
            val fy = ((ev.y - r.top) / r.height()).coerceIn(0f, 1f)
            return fx to fy
        }

        override fun onTouchEvent(ev: MotionEvent): Boolean {
            val b = bmp ?: return false
            val r = imgRect()
            val tolX = dp(30) / r.width(); val tolY = dp(30) / r.height()
            when (ev.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    val (fx, fy) = toFrac(ev)
                    val corners = listOf(cropL to cropT, cropR to cropT, cropR to cropB, cropL to cropB)
                    corner = corners.indexOfFirst { (cx, cy) -> kotlin.math.abs(fx - cx) <= tolX && kotlin.math.abs(fy - cy) <= tolY }
                    when {
                        corner >= 0 -> mode = 2
                        fx in cropL..cropR && fy in cropT..cropB -> { mode = 1; grabDX = fx - cropL; grabDY = fy - cropT }
                        else -> { mode = 3; anchorX = fx; anchorY = fy; cropL = fx; cropT = fy; cropR = fx; cropB = fy }
                    }
                    parent?.requestDisallowInterceptTouchEvent(true)
                    return true
                }
                MotionEvent.ACTION_MOVE -> {
                    if (mode == 0) return false
                    val (fx, fy) = toFrac(ev)
                    when (mode) {
                        1 -> {
                            val w = cropR - cropL
                            val h = cropB - cropT
                            cropL = (fx - grabDX).coerceIn(0f, 1f - w); cropR = cropL + w
                            cropT = (fy - grabDY).coerceIn(0f, 1f - h); cropB = cropT + h
                        }
                        2, 3 -> {
                            val ax = if (mode == 3) anchorX else when (corner) { 0, 3 -> cropR; else -> cropL }
                            val ay = if (mode == 3) anchorY else when (corner) { 0, 1 -> cropB; else -> cropT }
                            var nl = minOf(ax, fx)
                            var nr = maxOf(ax, fx)
                            var nt = minOf(ay, fy)
                            var nb = maxOf(ay, fy)
                            val a = aspect
                            if (a != null) {
                                var fh = (nr - nl) * b.width / (a * b.height)
                                if (ay == nt) { nb = nt + fh; if (nb > 1f) { nb = 1f; fh = nb - nt; nr = nl + fh * a * b.height / b.width } }
                                else { nt = nb - fh; if (nt < 0f) { nt = 0f; fh = nb - nt; nr = nl + fh * a * b.height / b.width } }
                            }
                            if (nr - nl >= 0.04f && nb - nt >= 0.04f) { cropL = nl; cropR = nr; cropT = nt; cropB = nb }
                        }
                    }
                    invalidate()
                    return true
                }
                MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> { mode = 0; corner = -1; return true }
            }
            return super.onTouchEvent(ev)
        }
    }

    private fun showEditPhoto() {
        val type = "photos"
        val item = lists[type]?.firstOrNull { it.key == selectedKey } ?: return
        val file = item.key.substringAfter('/')
        val withYear = Regex("^(\\d+) - (.*) - (\\d{4})(\\.[^.]+)$").matchEntire(file)
        val noYear = Regex("^(\\d+) - (.*?)(\\.[^.]+)$").matchEntire(file)
        val num: String; val oldTitle: String; val year: String?; val ext: String
        if (withYear != null) {
            num = withYear.groupValues[1]; oldTitle = withYear.groupValues[2]
            year = withYear.groupValues[3]; ext = withYear.groupValues[4]
        } else if (noYear != null) {
            num = noYear.groupValues[1]; oldTitle = noYear.groupValues[2]
            year = null; ext = noYear.groupValues[3]
        } else {
            toast("This item's name can't be edited here.")
            return
        }
        setStatus("Loading the photo…", type)
        thread {
            try {
                val bytes = Api.fetchFileBytes(this, item.key)
                val fmt = imageFormatOf(bytes)
                val raw = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                if (raw == null) {
                    post { setStatus("Edit failed: the photo could not be read.", type) }
                    return@thread
                }
                val orientation = exifOrientationOf(bytes)
                val hadExifRotation = orientation != 0 && orientation != 1 &&
                    orientation != ExifInterface.ORIENTATION_UNDEFINED
                val oriented = applyExifOrientation(raw, orientation)
                post {
                    setStatus("", type)
                    buildEditPhotoDialog(item, bytes, oriented, fmt, hadExifRotation, num, oldTitle, year, ext)
                }
            } catch (e: Exception) {
                Diag.record(this, "editPhoto load", e)
                post { setStatus("Edit failed: ${e.message}", type) }
            }
        }
    }

    private fun buildEditPhotoDialog(item: MediaItem, origBytes: ByteArray, startBmp: Bitmap,
                                     fmt: String, hadExifRotation: Boolean,
                                     num: String, oldTitle: String, year: String?, ext: String) {
        val type = "photos"
        val isGif = fmt == "gif"
        var workBmp = startBmp
        var pixelsDirty = hadExifRotation && !isGif

        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(18), dp(8), dp(18), dp(4))
        }
        col.addView(label("Rename Photo", 13f, SUB))
        val titleInput = field("Title").apply { setText(oldTitle); setSelection(text.length) }
        col.addView(titleInput)

        var cropView: PhotoCropView? = null
        if (isGif) {
            col.addView(label("GIF photos can't be edited here — rename still works.", 13f, SUB))
        } else {
            val cv = PhotoCropView()
            cv.bmp = workBmp
            cropView = cv
            col.addView(label("Orientation", 13f, SUB))
            val orow = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
            fun obtn(text: String, onClick: () -> Unit): Button {
                val b = darkButton(text)
                b.setOnClickListener { onClick() }
                orow.addView(b, LinearLayout.LayoutParams(0, dp(42), 1f).apply { marginEnd = dp(6) })
                return b
            }
            obtn("⟲ Left") { workBmp = rotateBitmap(workBmp, -90f); pixelsDirty = true; cv.bmp = workBmp; cv.resetCrop(); cv.requestLayout(); cv.invalidate() }
            obtn("⟳ Right") { workBmp = rotateBitmap(workBmp, 90f); pixelsDirty = true; cv.bmp = workBmp; cv.resetCrop(); cv.requestLayout(); cv.invalidate() }
            obtn("Flip ↔") { workBmp = flipBitmap(workBmp, true); pixelsDirty = true; cv.bmp = workBmp; cv.invalidate() }
            obtn("Flip ↕") { workBmp = flipBitmap(workBmp, false); pixelsDirty = true; cv.bmp = workBmp; cv.invalidate() }
            col.addView(orow)
            col.addView(label("Crop Photo — drag the box to move it, a corner to resize, or drag outside it to draw a new one.", 13f, SUB))
            val arow = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
            val aspectBtns = ArrayList<Button>()
            fun abtn(text: String, a: Float?) {
                val b = darkButton(text)
                b.setOnClickListener {
                    // "Original" follows the current bitmap (a rotate
                    // swaps its aspect); the rest are fixed values.
                    val useA = if (text == "Original") {
                        val bb = cv.bmp; if (bb != null && bb.height > 0) bb.width.toFloat() / bb.height else null
                    } else a
                    cv.applyAspect(useA)
                    for (x in aspectBtns) x.alpha = if (x === b) 1f else 0.55f
                }
                aspectBtns.add(b)
                arow.addView(b, LinearLayout.LayoutParams(0, dp(38), 1f).apply { marginEnd = dp(6) })
            }
            abtn("Free", null)
            abtn("Original", null)
            abtn("Square", 1f)
            abtn("4:3", 4f / 3f)
            abtn("3:2", 3f / 2f)
            abtn("16:9", 16f / 9f)
            for (x in aspectBtns) x.alpha = 0.55f
            aspectBtns[0].alpha = 1f
            col.addView(arow)
            col.addView(cv, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = dp(8) })
            if (hadExifRotation) col.addView(label("This photo was stored sideways; it opens upright here, and saving bakes that in.", 12.5f, SUB))
        }

        val scroll = android.widget.ScrollView(this).apply { addView(col) }
        AlertDialog.Builder(this)
            .setTitle("Edit Photo")
            .setView(scroll)
            .setPositiveButton("Save") { _, _ ->
                val newTitle = titleInput.text.toString().trim().replace('/', '-')
                val titleChanged = newTitle.isNotEmpty() && newTitle != oldTitle
                val cv = cropView
                val cropFull = cv == null || cv.cropIsFull()
                val doPixels = !isGif && (pixelsDirty || !cropFull)
                if (!titleChanged && !doPixels) return@setPositiveButton
                val srcBmp = workBmp
                val cl = cv?.cropL ?: 0f; val ct = cv?.cropT ?: 0f
                val cr = cv?.cropR ?: 1f; val cb = cv?.cropB ?: 1f
                setStatus("Saving the photo…", type)
                thread {
                    try {
                        if (doPixels) {
                            // Park the untouched original first, then
                            // overwrite the same key with the edit.
                            Api.putBytes(this, "inbox/originals/" + item.key.substringAfter('/'), origBytes)
                            var outBmp = srcBmp
                            if (!cropFull) {
                                val x = (cl * srcBmp.width).toInt().coerceIn(0, srcBmp.width - 1)
                                val y = (ct * srcBmp.height).toInt().coerceIn(0, srcBmp.height - 1)
                                val w = ((cr - cl) * srcBmp.width).toInt().coerceIn(1, srcBmp.width - x)
                                val h = ((cb - ct) * srcBmp.height).toInt().coerceIn(1, srcBmp.height - y)
                                outBmp = Bitmap.createBitmap(srcBmp, x, y, w, h)
                            }
                            val baos = ByteArrayOutputStream()
                            if (fmt == "png") outBmp.compress(Bitmap.CompressFormat.PNG, 100, baos)
                            else outBmp.compress(Bitmap.CompressFormat.JPEG, 90, baos)
                            Api.putBytes(this, item.key, baos.toByteArray())
                            adapter.evict(item.key)
                        }
                        if (titleChanged) {
                            val newFile = if (year != null) "$num - $newTitle - $year$ext" else "$num - $newTitle$ext"
                            Api.rename(this, item.key, "$type/$newFile")
                        }
                        post {
                            selectedKey = null
                            toast("Photo saved.")
                            refresh(type, keepOrder = false)
                        }
                    } catch (e: Exception) {
                        Diag.record(this, "editPhoto save", e)
                        post { setStatus("Save failed: ${e.message}", type) }
                    }
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun showRename() {
        val type = currentType
        val item = lists[type]?.firstOrNull { it.key == selectedKey } ?: return
        val kind = kindName(type)
        val file = item.key.substringAfter('/')
        val withYear = Regex("^(\\d+) - (.*) - (\\d{4})(\\.[^.]+)$").matchEntire(file)
        val noYear = Regex("^(\\d+) - (.*?)(\\.[^.]+)$").matchEntire(file)
        val num: String; val oldTitle: String; val year: String?; val ext: String
        if (withYear != null) {
            num = withYear.groupValues[1]; oldTitle = withYear.groupValues[2]
            year = withYear.groupValues[3]; ext = withYear.groupValues[4]
        } else if (noYear != null) {
            num = noYear.groupValues[1]; oldTitle = noYear.groupValues[2]
            year = null; ext = noYear.groupValues[3]
        } else {
            toast("This item's name can't be edited here.")
            return
        }
        val input = field("Title").apply { setText(oldTitle); setSelection(text.length) }
        AlertDialog.Builder(this)
            .setTitle("Rename $kind")
            .setMessage("Only the title changes — the item keeps its number, year, and place in the order.")
            .setView(input)
            .setPositiveButton("Rename") { _, _ ->
                val newTitle = input.text.toString().trim().replace('/', '-')
                if (newTitle.isEmpty() || newTitle == oldTitle) return@setPositiveButton
                val newFile = if (year != null) "$num - $newTitle - $year$ext" else "$num - $newTitle$ext"
                val newKey = "$type/$newFile"
                setStatus("Renaming…", type)
                thread {
                    try {
                        Api.rename(this, item.key, newKey)
                        post {
                            selectedKey = null
                            toast("Renamed to “$newTitle”.")
                            refresh(type, keepOrder = false)
                        }
                    } catch (e: Exception) {
                        Diag.record(this, "rename", e)
                        post { setStatus("Rename failed: ${e.message}", type) }
                    }
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    /**
     * Multi-select media move (v1.17): pick where the checkbox-selected
     * block goes. The dialog lists the UNSELECTED items of the current
     * tab in full-list order, each with its 1-based full-list position
     * — tapping one inserts the block immediately BEFORE it — plus a
     * final "Move to the end" row. (The list is the adapter's filtered
     * display's opposite: it is built from lists[type] itself, so a
     * search filter on the tab never changes the positions shown or
     * the order operated on.) The splice is LOCAL only: the selected
     * items are removed keeping their relative order and inserted as
     * one block; the tab is marked dirty and nothing reaches the
     * server until Save Order.
     */
    private fun showMoveSelectedMediaDialog() {
        val type = currentType
        val items = lists[type] ?: return
        val selectedKeys = moveSelection[type] ?: return
        if (selectedKeys.isEmpty()) return
        val block = items.filter { it.key in selectedKeys }
        if (block.isEmpty()) {
            selectedKeys.clear()
            updateMoveButton()
            return
        }
        val unselected = items.withIndex().filter { it.value.key !in selectedKeys }
        val labels = ArrayList<String>()
        for ((fullIdx, item) in unselected) labels.add("${fullIdx + 1}. ${item.title}")
        labels.add("Move to the end")
        AlertDialog.Builder(this)
            .setTitle("Move ${block.size} selected files")
            .setItems(labels.toTypedArray()) { _, which ->
                val rest = items.filter { it.key !in selectedKeys }
                val insertAt = if (which >= unselected.size) {
                    rest.size
                } else {
                    val targetKey = unselected[which].value.key
                    rest.indexOfFirst { it.key == targetKey }
                }
                if (insertAt >= 0) {
                    val newList = rest.toMutableList()
                    newList.addAll(insertAt, block)
                    val n = block.size
                    selectedKeys.clear()
                    if (newList.map { it.key } == items.map { it.key }) {
                        // Already exactly there (e.g. every item
                        // selected and "Move to the end" tapped): no
                        // order change, so no dirty mark — just clear
                        // the checkboxes.
                        adapter.notifyDataSetChanged()
                        updateMoveButton()
                        setStatus("$n files are already in that position \u2014 no change.", type)
                    } else {
                        lists[type] = newList
                        dirty[type] = true
                        adapter.notifyDataSetChanged()
                        updateSaveOrder()
                        updateMoveButton()
                        setStatus("$n files moved \u2014 Save Order to save.", type)
                        toast("$n files moved \u2014 Save Order to save.")
                    }
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    // ------------------------------------------------------------ list adapter

    inner class MediaAdapter : RecyclerView.Adapter<MediaAdapter.Holder>() {
        var touchHelper: ItemTouchHelper? = null
        private val cache = HashMap<String, Bitmap>()
        private val loading = HashSet<String>()
        private val thumbPool = Executors.newFixedThreadPool(3)

        inner class Holder(v: View) : RecyclerView.ViewHolder(v) {
            val thumb: ImageView = v.findViewById(1)
            val glyph: TextView = v.findViewById(2)
            val title: TextView = v.findViewById(3)
            val sub: TextView = v.findViewById(4)
            val del: Button = v.findViewById(5)
            val handle: TextView = v.findViewById(6)
            val moveCheck: CheckBox = v.findViewById(7)
        }

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): Holder {
            val row = LinearLayout(this@MainActivity).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                setBackgroundColor(CARD)
                setPadding(dp(10), dp(8), dp(6), dp(8))
                layoutParams = RecyclerView.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { bottomMargin = dp(6) }
            }
            val frame = FrameLayout(this@MainActivity).apply {
                layoutParams = LinearLayout.LayoutParams(dp(58), dp(58)).apply { marginEnd = dp(10) }
                setBackgroundColor(BG)
            }
            val img = ImageView(this@MainActivity).apply { id = 1; scaleType = ImageView.ScaleType.CENTER_CROP }
            frame.addView(img, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
            val glyph = TextView(this@MainActivity).apply {
                id = 2; text = "▶"; textSize = 18f; setTextColor(GOLD); gravity = Gravity.CENTER
            }
            frame.addView(glyph, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
            row.addView(frame)
            // Multi-select move checkbox (v1.17), a clickable child of
            // the row like the Dropbox browser's: a tap on it is
            // consumed by the checkbox itself, so it never reaches the
            // row's tap/selection handler, and drag is untouched (drag
            // starts only from the ≡ handle's own touch listener).
            row.addView(CheckBox(this@MainActivity).apply { id = 7 })
            val texts = LinearLayout(this@MainActivity).apply {
                orientation = LinearLayout.VERTICAL
                layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
            }
            texts.addView(TextView(this@MainActivity).apply { id = 3; textSize = 14.5f; setTextColor(TEXT) })
            texts.addView(TextView(this@MainActivity).apply { id = 4; textSize = 12f; setTextColor(SUB) })
            row.addView(texts)
            val handle = TextView(this@MainActivity).apply {
                id = 6; text = "≡"; textSize = 22f; setTextColor(SUB); gravity = Gravity.CENTER
                setPadding(dp(8), dp(8), dp(8), dp(8))
            }
            row.addView(handle)
            val del = Button(this@MainActivity).apply {
                id = 5; text = "✕"; textSize = 14f; setTextColor(Color.rgb(255, 154, 138)); setBackgroundColor(Color.TRANSPARENT)
            }
            row.addView(del, LinearLayout.LayoutParams(dp(44), dp(48)))
            return Holder(row)
        }

        /** Drops one cached thumbnail (after an Edit Photo save replaced the file). */
        fun evict(key: String) {
            synchronized(cache) { cache.remove(key) }
        }

        fun refreshPositions() {
            // Position numbers are part of the sub-line; simplest correct
            // refresh is a full rebind of visible rows.
            notifyDataSetChanged()
        }

        override fun getItemCount(): Int = visibleMediaItems().size

        override fun onBindViewHolder(h: Holder, position: Int) {
            val items = lists[currentType] ?: return
            val vis = visibleMediaItems()
            if (position !in vis.indices) return
            // The position shown is the item's place in the FULL list,
            // so numbering stays truthful while a search is filtering.
            val (fullIdx, item) = vis[position]
            h.glyph.text = if (currentType == "audio") "🎵" else "▶"
            h.title.text = item.title
            h.sub.text = "${fullIdx + 1} of ${items.size} · ${fmtSize(item.size)}"
            h.del.setOnClickListener { confirmDelete(item) }
            // Checkbox state comes from the move-selection set for
            // this tab (keyed by item key, so it is correct on filtered
            // rows too). Clear the listener before setting isChecked so
            // a recycled holder never fires for the previous item; the
            // new listener touches ONLY the set and the Move button —
            // never the row selection, never a rebind.
            val boundType = currentType
            h.moveCheck.setOnCheckedChangeListener(null)
            h.moveCheck.isChecked = moveSelection[boundType]?.contains(item.key) == true
            h.moveCheck.setOnCheckedChangeListener { _, checked ->
                val set = moveSelection.getOrPut(boundType) { mutableSetOf() }
                if (checked) set.add(item.key) else set.remove(item.key)
                updateMoveButton()
            }
            h.itemView.background = if (item.key == selectedKey) selectedBg() else roundedBg(CARD)
            h.itemView.setOnClickListener {
                selectedKey = if (selectedKey == item.key) null else item.key
                notifyDataSetChanged()
                updateRename()
            }
            h.handle.setOnTouchListener { _, ev ->
                if (ev.actionMasked == MotionEvent.ACTION_DOWN) touchHelper?.startDrag(h)
                false
            }
            val thumbKey = when {
                currentType == "photos" -> item.key
                item.poster -> item.posterKey
                else -> null
            }
            h.thumb.tag = thumbKey ?: ""
            if (thumbKey == null) {
                h.thumb.setImageBitmap(null)
                h.glyph.visibility = View.VISIBLE
                return
            }
            val cached = cache[thumbKey]
            if (cached != null) {
                h.thumb.setImageBitmap(cached)
                h.glyph.visibility = View.GONE
            } else {
                h.thumb.setImageBitmap(null)
                h.glyph.visibility = if (currentType == "videos") View.VISIBLE else View.GONE
                loadThumb(thumbKey)
            }
        }

        private fun loadThumb(key: String) {
            synchronized(loading) { if (!loading.add(key)) return }
            thumbPool.execute {
                try {
                    val bytes = Api.fetchFileBytes(this@MainActivity, key)
                    val bmp = decodeOriented(bytes, 4)
                    if (bmp != null) {
                        synchronized(cache) { cache[key] = bmp }
                        post { notifyDataSetChanged() }
                    }
                } catch (e: Exception) {
                    Diag.record(this@MainActivity, "thumb $key", e)
                } finally {
                    synchronized(loading) { loading.remove(key) }
                }
            }
        }
    }

    // ------------------------------------------------------------ adding media

    private var pendingType = "photos"
    private var queueActive = false
    private var queueCancelled = false
    // Shared year for a multi-file upload queue (v1.16): set when Luddy
    // picks "Use this year for all" in the queue's opening dialog, then
    // every per-file dialog in that queue asks Title only. Null means
    // each file is asked for its own year, as before.
    private var queueSharedYear: String? = null
    private var progressDialog: AlertDialog? = null
    private var progressBar: ProgressBar? = null
    private var progressText: TextView? = null

    // The import menu is one common list on every media tab (Luddy,
    // 2026-11-14): the same ten sources in the same order everywhere;
    // only the first entry's wording follows the tab (Photo / Video / Audio).
    // Sources not yet connected (Google Drive, Microsoft OneDrive) stay
    // on the list and say so when tapped.
    private fun showSourceSheet(type: String) {
        pendingType = type
        val kind = kindName(type)
        val options = arrayOf(
            "From Device - $kind",
            "From Device - Files",
            "From Web Link",
            "From Network Share",
            "From Dropbox",
            "From Google Drive",
            "From Microsoft OneDrive",
            "From Amazon Photo",
            "From Facebook",
            "From Instagram")
        AlertDialog.Builder(this)
            .setTitle("Add a $kind")
            .setItems(options) { _, which ->
                when (which) {
                    0 -> launchGallery(type)
                    1 -> launchFiles(type, exportMode = false)
                    2 -> showImportUrlDialog(type)
                    3 -> showWebDavDialog(type)
                    4 -> openDropboxBrowser(type)
                    5 -> showNotConnectedDialog("Google Drive")
                    6 -> showNotConnectedDialog("Microsoft OneDrive")
                    7 -> showGuidedDialog("amazon")
                    8 -> showGuidedDialog("facebook")
                    9 -> showGuidedDialog("instagram")
                }
            }
            .show()
    }

    private fun launchGallery(type: String) {
        pendingType = type
        val intent = Intent(Intent.ACTION_GET_CONTENT).apply {
            this.type = when (type) {
                "photos" -> "image/*"
                "videos" -> "video/*"
                else -> "audio/*"
            }
            addCategory(Intent.CATEGORY_OPENABLE)
            putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true)
            navDeviceInitialUri()?.let { putExtra(DocumentsContract.EXTRA_INITIAL_URI, it) }
        }
        startActivityForResult(Intent.createChooser(intent, "Choose media"), REQ_GALLERY)
    }

    private fun launchFiles(type: String, exportMode: Boolean) {
        pendingType = type
        val intent = Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
            addCategory(Intent.CATEGORY_OPENABLE)
            this.type = when {
                exportMode -> "*/*"
                type == "photos" -> "image/*"
                type == "videos" -> "video/*"
                else -> "audio/*"
            }
            putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true)
            navDeviceInitialUri()?.let { putExtra(DocumentsContract.EXTRA_INITIAL_URI, it) }
        }
        startActivityForResult(intent, REQ_FILES)
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (resultCode != RESULT_OK || data == null) return
        if (requestCode != REQ_GALLERY && requestCode != REQ_FILES) return
        val uris = ArrayList<Uri>()
        data.clipData?.let { cd -> for (i in 0 until cd.itemCount) cd.getItemAt(i).uri?.let { uris.add(it) } }
        if (uris.isEmpty()) data.data?.let { uris.add(it) }
        if (uris.isEmpty()) return
        navSaveDevicePlace(uris.first())
        startUploadQueue(pendingType, uris)
    }

    private fun displayName(uri: Uri): String? {
        return try {
            contentResolver.query(uri, null, null, null, null)?.use { c ->
                val idx = c.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                if (idx >= 0 && c.moveToFirst()) c.getString(idx) else null
            }
        } catch (e: Exception) { null } ?: uri.lastPathSegment?.substringAfterLast('/')
    }

    private fun nextNumber(items: List<MediaItem>): Int {
        var max = 0
        for (it in items) {
            val m = Regex("^(\\d+)").find(it.key.substringAfterLast('/')) ?: continue
            max = maxOf(max, m.groupValues[1].toIntOrNull() ?: 0)
        }
        return max + 1
    }

    private fun extFor(name: String, mime: String?, type: String): String {
        val allowed = when (type) {
            "photos" -> setOf("jpg", "jpeg", "png", "webp", "gif")
            "videos" -> setOf("mp4", "webm")
            else -> setOf("mp3", "m4a", "wav", "ogg", "flac", "aac")
        }
        val suffix = name.substringAfterLast('.', "").lowercase()
        if (suffix in allowed) return ".$suffix"
        return when (mime) {
            "image/jpeg" -> ".jpg"
            "image/png" -> ".png"
            "image/webp" -> ".webp"
            "image/gif" -> ".gif"
            "video/mp4" -> ".mp4"
            "video/webm" -> ".webm"
            "audio/mpeg", "audio/mp3" -> ".mp3"
            "audio/mp4", "audio/x-m4a" -> ".m4a"
            "audio/wav", "audio/x-wav", "audio/wave" -> ".wav"
            "audio/ogg" -> ".ogg"
            "audio/flac", "audio/x-flac" -> ".flac"
            "audio/aac" -> ".aac"
            else -> when (type) {
                "photos" -> ".jpg"
                "videos" -> ".mp4"
                else -> ".mp3"
            }
        }
    }

    private fun startUploadQueue(type: String, uris: List<Uri>) {
        if (queueActive) { toast("An upload is already running."); return }
        queueActive = true
        queueCancelled = false
        setStatus("Preparing uploads…", pendingType)
        thread {
            val fresh = try { Api.list(this, type) } catch (e: Exception) { lists[type] ?: emptyList() }
            val start = nextNumber(fresh)
            post {
                if (uris.size > 1) askSharedYearForQueue(type, uris, start)
                else { queueSharedYear = null; processQueueItem(type, uris, 0, start) }
            }
        }
    }

    /** The year parsed from a patterned file name ("NNN - Title - YYYY.ext"), or null. */
    private fun parseQueueYear(displayName: String): String? {
        val m = Regex("^\\d+\\s*-\\s*(.+?)\\s*-\\s*(\\d{4})\\.[^.]+$").find(displayName)
        return m?.groupValues?.get(2)
    }

    /**
     * Multi-file queues only (v1.16): before the per-file dialogs, ask
     * ONCE whether every file in this upload shares one year. "Use this
     * year for all" validates the year and keeps the dialog open on an
     * invalid entry; "Ask for each file" proceeds exactly as before.
     * The field is prefilled when every file name parses to the SAME
     * year, and left blank otherwise.
     */
    private fun askSharedYearForQueue(type: String, uris: List<Uri>, start: Int) {
        val years = uris.map { parseQueueYear(displayName(it) ?: "") }
        val prefill = if (years.all { it != null } && years.distinct().size == 1) years.first() ?: "" else ""
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(8), dp(20), 0)
        }
        col.addView(TextView(this).apply {
            text = "${uris.size} files selected. Give them all the same year, or name each file with its own year."
            textSize = 13f; setTextColor(SUB)
        })
        val yearF = field("Year (4 digits)").apply {
            setText(prefill)
            inputType = android.text.InputType.TYPE_CLASS_NUMBER
        }
        col.addView(yearF)
        val dialog = AlertDialog.Builder(this)
            .setTitle("Year for these files")
            .setView(col)
            .setPositiveButton("Use this year for all", null)
            .setNegativeButton("Ask for each file") { _, _ ->
                queueSharedYear = null
                processQueueItem(type, uris, 0, start)
            }
            .setOnCancelListener {
                queueSharedYear = null
                queueActive = false
                toast("Upload cancelled.")
                refresh(type, keepOrder = false)
            }
            .create()
        dialog.show()
        dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
            val y = yearF.text.toString().trim()
            if (!Regex("^\\d{4}$").matches(y)) {
                yearF.error = "Enter a 4-digit year"
                return@setOnClickListener
            }
            queueSharedYear = y
            dialog.dismiss()
            processQueueItem(type, uris, 0, start)
        }
    }

    private fun processQueueItem(type: String, uris: List<Uri>, index: Int, nextNum: Int) {
        if (index >= uris.size || queueCancelled) {
            queueActive = false
            queueSharedYear = null
            toast(if (queueCancelled) "Upload cancelled." else "Upload complete.")
            refresh(type, keepOrder = false)
            return
        }
        val uri = uris[index]
        val name = displayName(uri) ?: "upload"
        showProgress("Adding ${index + 1} of ${uris.size}", "Preparing “$name”…", indeterminate = true)
        thread {
            val staged: File? = try {
                val dir = File(cacheDir, "stage").apply { mkdirs() }
                val f = File(dir, "stage-$index-" + name.replace(Regex("[^A-Za-z0-9._-]"), "_"))
                contentResolver.openInputStream(uri)?.use { input ->
                    f.outputStream().use { input.copyTo(it) }
                } ?: throw IllegalStateException("Could not open the picked file.")
                f
            } catch (e: Exception) {
                Diag.record(this, "stage", e)
                null
            }
            post {
                hideProgress()
                if (staged == null) {
                    toast("Could not read “$name” — skipped.")
                    processQueueItem(type, uris, index + 1, nextNum)
                } else {
                    uploadCursor = nextNum
                    askTitleYearThenUpload(type, staged, name, queueSharedYear) { success ->
                        staged.delete()
                        processQueueItem(type, uris, index + 1, if (success) nextNum + 1 else nextNum)
                    }
                }
            }
        }
    }

    /** Asks Title + Year (prefilled from the file name when it follows the pattern), then uploads the staged file. */
    private fun askTitleYearThenUpload(type: String, file: File, displayName: String, sharedYear: String? = null, onDone: (Boolean) -> Unit) {
        var fired = false
        val doneOnce: (Boolean) -> Unit = { ok -> if (!fired) { fired = true; onDone(ok) } }
        var preTitle = displayName.substringBeforeLast('.', displayName)
        var preYear = ""
        val m = Regex("^\\d+\\s*-\\s*(.+?)\\s*-\\s*(\\d{4})\\.[^.]+$").find(displayName)
        if (m != null) { preTitle = m.groupValues[1]; preYear = m.groupValues[2] }
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(8), dp(20), 0)
        }
        val titleF = field("Title").apply { setText(preTitle) }
        // Shared-year queues (v1.16) ask Title only: the year is already
        // settled for the whole upload and is shown as a plain line
        // below instead of a field.
        val yearF: EditText? = if (sharedYear != null) null else field("Year (4 digits)").apply {
            setText(preYear)
            inputType = android.text.InputType.TYPE_CLASS_NUMBER
        }
        col.addView(TextView(this).apply { text = "“$displayName” will be added as the next ${kindLower(type)}. The name follows the page pattern: number - Title - Year."; textSize = 13f; setTextColor(SUB) })
        col.addView(titleF)
        if (sharedYear != null) {
            col.addView(TextView(this).apply {
                text = "Year: $sharedYear (same for all files in this upload)"
                textSize = 13f; setTextColor(SUB)
                setPadding(0, dp(10), 0, 0)
            })
        } else if (yearF != null) {
            col.addView(yearF)
        }
        AlertDialog.Builder(this)
            .setTitle("Name this ${kindLower(type)}")
            .setView(col)
            .setPositiveButton("Upload") { _, _ ->
                val title = titleF.text.toString().trim().replace('/', '-')
                val year = sharedYear ?: (yearF?.text?.toString()?.trim() ?: "")
                if (title.isEmpty() || !Regex("^\\d{4}$").matches(year)) {
                    toast("A title and a 4-digit year are needed — item skipped.")
                    doneOnce(false)
                    return@setPositiveButton
                }
                val mime = try { contentResolver.getType(Uri.fromFile(file)) } catch (e: Exception) { null }
                val ext = extFor(displayName, mime, type)
                val key = "$type/" + String.format("%03d", nextNumberForUpload(type)) + " - $title - $year$ext"
                uploadStagedFile(type, file, key, doneOnce)
            }
            .setNegativeButton("Skip") { _, _ -> doneOnce(false) }
            .setOnCancelListener { doneOnce(false) }
            .show()
    }

    // The queue tracks its own running number; this is refreshed from the
    // server list when a standalone (non-queue) upload starts.
    @Volatile private var standaloneNext: Int = -1
    private fun nextNumberForUpload(type: String): Int {
        if (queueActive) return uploadCursor
        return standaloneNext
    }
    @Volatile private var uploadCursor: Int = 1

    // ------------------------------------------------------------ posters
    // A video's page poster is a frame a few seconds in, at most 640 px on
    // the long edge, JPEG quality 80 — the same shape as the posters the
    // migration made. Uploaded under the video's poster key; the
    // server-side poster sweep backstops anything the app cannot make.

    private fun posterJpeg(frame: Bitmap?): ByteArray? {
        if (frame == null) return null
        val scale = minOf(1f, 640f / maxOf(frame.width, frame.height).toFloat())
        val bmp = if (scale < 1f) Bitmap.createScaledBitmap(frame, (frame.width * scale).toInt().coerceAtLeast(1), (frame.height * scale).toInt().coerceAtLeast(1), true) else frame
        val out = ByteArrayOutputStream()
        val ok = bmp.compress(Bitmap.CompressFormat.JPEG, 80, out)
        if (bmp !== frame) bmp.recycle()
        frame.recycle()
        return if (ok) out.toByteArray() else null
    }

    private fun posterFromFile(file: File): ByteArray? {
        val mmr = MediaMetadataRetriever()
        return try {
            mmr.setDataSource(file.absolutePath)
            posterJpeg(mmr.getFrameAtTime(5_000_000, MediaMetadataRetriever.OPTION_CLOSEST_SYNC) ?: mmr.getFrameAtTime(0, MediaMetadataRetriever.OPTION_CLOSEST_SYNC))
        } catch (e: Exception) { null } finally { try { mmr.release() } catch (e: Exception) { } }
    }

    private fun posterFromUrl(url: String): ByteArray? {
        // Mirror the server's Dropbox normalization so the retriever reaches the file itself.
        val direct = if (url.contains("dropbox.com")) url.replace("dl=0", "dl=1") else url
        val mmr = MediaMetadataRetriever()
        return try {
            mmr.setDataSource(direct, HashMap<String, String>())
            posterJpeg(mmr.getFrameAtTime(5_000_000, MediaMetadataRetriever.OPTION_CLOSEST_SYNC) ?: mmr.getFrameAtTime(0, MediaMetadataRetriever.OPTION_CLOSEST_SYNC))
        } catch (e: Exception) { null } finally { try { mmr.release() } catch (e: Exception) { } }
    }

    /** Best-effort poster upload for a stored video; failure never reaches the caller. */
    private fun uploadPoster(videoKey: String, jpeg: ByteArray?): Boolean {
        if (jpeg == null || jpeg.isEmpty()) return false
        return try {
            Api.putBytes(this, MediaItem(videoKey, "", 0, false).posterKey, jpeg)
            true
        } catch (e: Exception) {
            Diag.record(this, "poster $videoKey", e)
            false
        }
    }

    private fun uploadStagedFile(type: String, file: File, key: String, onDone: (Boolean) -> Unit) {
        showProgress("Uploading", key.substringAfterLast('/'), indeterminate = false)
        thread {
            try {
                val cb: (Long, Long) -> Unit = { sent, total ->
                    post { setProgress(if (total > 0) (sent * 1000 / total).toInt() else 0, "${fmtSize(sent)} of ${fmtSize(total)}") }
                }
                if (type == "videos") Api.multipartUpload(this, key, file, cb) else Api.putFile(this, key, file, cb)
                val posterOk = if (type == "videos") uploadPoster(key, posterFromFile(file)) else false
                post {
                    hideProgress()
                    toast("Added: ${key.substringAfterLast('/')}" + if (type == "videos") (if (posterOk) " · poster added" else " · poster comes from the sweep") else "")
                    onDone(true)
                }
            } catch (e: Exception) {
                Diag.record(this, "upload $key", e)
                post {
                    hideProgress()
                    AlertDialog.Builder(this)
                        .setTitle("Upload failed")
                        .setMessage("${e.message}\n\nTry this file again, or skip it?")
                        .setPositiveButton("Retry") { _, _ -> uploadStagedFile(type, file, key, onDone) }
                        .setNegativeButton("Skip") { _, _ -> onDone(false) }
                        .show()
                }
            }
        }
    }

    private fun showProgress(title: String, message: String, indeterminate: Boolean) {
        hideProgress()
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(22), dp(10), dp(22), 0)
        }
        progressText = TextView(this).apply { text = message; textSize = 13.5f; setTextColor(SUB) }
        progressBar = ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply {
            max = 1000
            isIndeterminate = indeterminate
        }
        col.addView(progressText)
        col.addView(progressBar)
        progressDialog = AlertDialog.Builder(this)
            .setTitle(title)
            .setView(col)
            .setNegativeButton("Cancel uploads") { _, _ -> queueCancelled = true }
            .setCancelable(false)
            .show()
    }

    private fun setProgress(v: Int, message: String) {
        progressBar?.isIndeterminate = false
        progressBar?.progress = v
        progressText?.text = message
    }

    private fun hideProgress() {
        try { progressDialog?.dismiss() } catch (e: Exception) { }
        progressDialog = null
    }

    // ------------------------------------------------------------ place memory
    // The app remembers where browsing left off, per source, so coming
    // back does not mean walking the whole path again (Luddy, 2026-11-14).
    // The place is kept for 24 hours; after that, browsing starts at the
    // top level again. The same store serves every browsable source.
    private fun navRememberedPath(source: String): String {
        val p = getSharedPreferences("nav_state", MODE_PRIVATE)
        val at = p.getLong("path_${source}_at", 0L)
        if (at == 0L) return ""
        if (System.currentTimeMillis() - at > 24L * 60L * 60L * 1000L) return ""
        return p.getString("path_$source", "") ?: ""
    }

    private fun navSavePath(source: String, path: String) {
        getSharedPreferences("nav_state", MODE_PRIVATE).edit()
            .putString("path_$source", path)
            .putLong("path_${source}_at", System.currentTimeMillis())
            .apply()
    }

    // The device pickers are Android's own screens, so the app cannot
    // hold them in place directly — the best it can do is hand the Files
    // picker the folder the last pick came from as its starting point
    // (EXTRA_INITIAL_URI), kept under the same 24-hour rule.
    private fun navDeviceInitialUri(): Uri? {
        val p = getSharedPreferences("nav_state", MODE_PRIVATE)
        val at = p.getLong("device_parent_at", 0L)
        if (at == 0L) return null
        if (System.currentTimeMillis() - at > 24L * 60L * 60L * 1000L) return null
        val stored = p.getString("device_parent_uri", null) ?: return null
        return try { Uri.parse(stored) } catch (e: Exception) { null }
    }

    private fun navSaveDevicePlace(uri: Uri) {
        try {
            val docId = DocumentsContract.getDocumentId(uri)
            if (!docId.contains('/')) return
            val parent = DocumentsContract.buildDocumentUri(uri.authority, docId.substringBeforeLast('/'))
            getSharedPreferences("nav_state", MODE_PRIVATE).edit()
                .putString("device_parent_uri", parent.toString())
                .putLong("device_parent_at", System.currentTimeMillis())
                .apply()
        } catch (e: Exception) {
            // Not a documents-provider pick (for example a media-store
            // pick) — there is no folder the app can hand back later.
        }
    }

    // The network-share dialog remembers the address it last used — the
    // share address is the "place" there — under the same 24-hour rule.
    private fun navWebDavAddress(): String {
        val p = getSharedPreferences("nav_state", MODE_PRIVATE)
        val at = p.getLong("webdav_at", 0L)
        if (at == 0L) return ""
        if (System.currentTimeMillis() - at > 24L * 60L * 60L * 1000L) return ""
        return p.getString("webdav_url", "") ?: ""
    }

    private fun navSaveWebDavAddress(u: String) {
        getSharedPreferences("nav_state", MODE_PRIVATE).edit()
            .putString("webdav_url", u)
            .putLong("webdav_at", System.currentTimeMillis())
            .apply()
    }

    // Dropbox picker thumbnails (2026-11-15): real previews — a photo's
    // thumbnail, a video's poster — fetched through the server (which
    // asks Dropbox for them), cached for the session, so files in the
    // Dropbox browser can be picked by sight. Same loading shape as
    // the library adapter's thumbnails. A file Dropbox cannot
    // thumbnail keeps the row's clearly generic glyph.
    private val dbxThumbCache = HashMap<String, Bitmap>()
    private val dbxThumbLoading = HashSet<String>()
    private val dbxThumbPool = Executors.newFixedThreadPool(3)

    private fun loadDbxThumb(path: String, onLoaded: () -> Unit) {
        synchronized(dbxThumbLoading) { if (!dbxThumbLoading.add(path)) return }
        dbxThumbPool.execute {
            try {
                val bytes = Api.dropboxThumb(this, path)
                val bmp = decodeOriented(bytes, 1)
                if (bmp != null) {
                    synchronized(dbxThumbCache) { dbxThumbCache[path] = bmp }
                    post { onLoaded() }
                }
            } catch (e: Exception) {
                Diag.record(this, "dbx thumb $path", e)
            } finally {
                synchronized(dbxThumbLoading) { dbxThumbLoading.remove(path) }
            }
        }
    }

    private fun openDropboxBrowser(type: String) {
        showDropboxBrowser(type, navRememberedPath("dropbox"))
    }

    private fun showNotConnectedDialog(name: String) {
        AlertDialog.Builder(this)
            .setTitle("From $name")
            .setMessage("$name is on the import list but is not connected yet. Connecting it takes the same one-time setup Dropbox got; until then this entry is a placeholder.")
            .setPositiveButton("OK", null)
            .show()
    }

    // ------------------------------------------------------------ Dropbox browse (Stage 2)
    // The server holds the Dropbox sign-in (connected once at
    // ljm3.me/oauth/dropbox); the app only browses and picks. Picking a
    // file asks Title + Year, then the server copies it straight from
    // Dropbox into the library — the file never passes through the phone.
    // File rows also carry checkboxes for multi-select (v1.16):
    // "Import selected (N)" imports several files at once under one
    // shared year, each file keeping its own name as its title; the
    // imports run sequentially so the server assigns each file its
    // next number in turn.

    private fun showDropboxBrowser(type: String, path: String, isFallback: Boolean = false) {
        showProgress("Dropbox", "Loading…", indeterminate = true)
        thread {
            try {
                val entries = Api.dropboxList(this, path)
                post { hideProgress(); showDropboxEntries(type, path, entries) }
            } catch (e: Exception) {
                Diag.record(this, "dropboxList", e)
                if (path.isNotEmpty() && !isFallback) {
                    // A remembered folder may have been moved or deleted:
                    // forget it and reopen at the top level rather than fail.
                    navSavePath("dropbox", "")
                    post { hideProgress(); toast("That Dropbox folder is no longer there — opening the top level."); showDropboxBrowser(type, "", isFallback = true) }
                } else {
                    post { hideProgress(); toast("Dropbox: ${e.message}") }
                }
            }
        }
    }

    private fun showDropboxEntries(type: String, path: String, entries: List<Api.DbxEntry>) {
        navSavePath("dropbox", path)
        val allowed = when (type) {
            "photos" -> setOf("jpg", "jpeg", "png", "webp", "gif")
            "videos" -> setOf("mp4", "webm")
            else -> setOf("mp3", "m4a", "wav", "ogg", "flac", "aac")
        }
        // One picker row: its text label, the Dropbox entry whose
        // thumbnail should show beside it (photo/video files only),
        // the file entry itself on file rows (null on folders, the Up
        // row, and the empty placeholder — those rows get no checkbox),
        // and the tap action — the same rows, order, and actions as
        // the text-only list this replaces; only the previews are new.
        class DbxRow(val label: String, val thumbEntry: Api.DbxEntry?, val entry: Api.DbxEntry?, val action: () -> Unit)
        val rows = ArrayList<DbxRow>()
        if (path.isNotEmpty()) {
            val parent = path.substringBeforeLast('/', "")
            rows.add(DbxRow("⬆  Up one level", null, null) { showDropboxBrowser(type, parent) })
        }
        var hidden = 0
        for (e in entries) {
            if (e.tag == "folder") {
                val p = e.path
                rows.add(DbxRow("📁  ${e.name}", null, null) { showDropboxBrowser(type, p) })
            } else if (e.name.substringAfterLast('.', "").lowercase() in allowed) {
                val thumb = if (type == "photos" || type == "videos") e else null
                rows.add(DbxRow("${e.name}  (${fmtSize(e.size)})", thumb, e) { showDropboxNameDialog(type, e) })
            } else hidden++
        }
        if (rows.isEmpty()) {
            rows.add(DbxRow("(nothing here that can be added)", null, null) { })
        }
        // Multi-select (v1.16): the paths of the file rows whose
        // checkbox is ticked. The browser dialog's positive button is
        // "Import selected (N)", shown and enabled only while N >= 1.
        val selectedPaths = LinkedHashSet<String>()
        var browserDialog: AlertDialog? = null
        fun updateImportButton() {
            val dlg = browserDialog ?: return
            val btn = try { dlg.getButton(AlertDialog.BUTTON_POSITIVE) } catch (e: Exception) { null } ?: return
            val n = selectedPaths.size
            btn.text = "Import selected ($n)"
            btn.visibility = if (n >= 1) View.VISIBLE else View.GONE
            btn.isEnabled = n >= 1
        }
        val adapter = object : BaseAdapter() {
            override fun getCount(): Int = rows.size
            override fun getItem(position: Int): Any = rows[position]
            override fun getItemId(position: Int): Long = position.toLong()
            override fun getView(position: Int, convertView: View?, parent: ViewGroup): View {
                val row = rows[position]
                val ll = LinearLayout(this@MainActivity).apply {
                    orientation = LinearLayout.HORIZONTAL
                    gravity = Gravity.CENTER_VERTICAL
                    background = roundedBg(CARD)
                    setPadding(dp(10), dp(8), dp(10), dp(8))
                }
                val e = row.thumbEntry
                if (e != null) {
                    val frame = FrameLayout(this@MainActivity).apply {
                        layoutParams = LinearLayout.LayoutParams(dp(64), dp(64)).apply { marginEnd = dp(10) }
                        setBackgroundColor(BG)
                    }
                    val img = ImageView(this@MainActivity).apply { scaleType = ImageView.ScaleType.CENTER_CROP }
                    val glyph = TextView(this@MainActivity).apply {
                        text = if (type == "videos") "▶" else "🖼"
                        textSize = 20f; setTextColor(GOLD); gravity = Gravity.CENTER
                    }
                    frame.addView(img, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
                    frame.addView(glyph, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
                    val cached = synchronized(dbxThumbCache) { dbxThumbCache[e.path] }
                    if (cached != null) {
                        img.setImageBitmap(cached)
                        glyph.visibility = View.GONE
                    } else {
                        loadDbxThumb(e.path) { notifyDataSetChanged() }
                    }
                    ll.addView(frame)
                }
                val fileEntry = row.entry
                if (fileEntry != null) {
                    // The checkbox toggles selection ONLY: it is a
                    // clickable child of the row, so a tap on it never
                    // reaches the row's tap/navigation, while tapping
                    // the row body keeps the single-file flow. Audio
                    // rows have no thumbnail frame but still get one.
                    val cb = CheckBox(this@MainActivity).apply {
                        setOnCheckedChangeListener(null)
                        isChecked = selectedPaths.contains(fileEntry.path)
                        setOnCheckedChangeListener { _, checked ->
                            if (checked) selectedPaths.add(fileEntry.path) else selectedPaths.remove(fileEntry.path)
                            updateImportButton()
                        }
                    }
                    ll.addView(cb)
                }
                ll.addView(TextView(this@MainActivity).apply {
                    text = row.label; textSize = 15f; setTextColor(TEXT)
                    layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
                })
                return ll
            }
        }
        val dialog = AlertDialog.Builder(this)
            .setTitle(if (path.isEmpty()) "Dropbox — top level" else "Dropbox — ${path.substringAfterLast('/')}")
            .setAdapter(adapter) { _, which -> rows[which].action() }
            .setPositiveButton("Import selected (0)", null)
            .setNegativeButton("Close", null)
            .create()
        browserDialog = dialog
        dialog.show()
        val importBtn = dialog.getButton(AlertDialog.BUTTON_POSITIVE)
        importBtn.visibility = View.GONE
        importBtn.isEnabled = false
        importBtn.setOnClickListener {
            val selected = rows.mapNotNull { it.entry }.filter { selectedPaths.contains(it.path) }
            if (selected.isEmpty()) return@setOnClickListener
            showDropboxMultiImport(type, selected, dialog)
        }
        if (hidden > 0) toast("$hidden file(s) here are not $type and are not shown.")
    }

    /** Trailing year of a Dropbox file name: the name minus its extension, then "- YYYY" at the end. */
    private fun dbxYearOf(name: String): String? {
        val base = name.substringBeforeLast('.', name)
        val m = Regex("^(.+?)\\s*-\\s*(\\d{4})$").find(base)
        return m?.groupValues?.get(2)
    }

    /** The single-flow title prefill for a Dropbox file name: name minus extension, minus a trailing "- YYYY". */
    private fun dbxTitleOf(name: String): String {
        var base = name.substringBeforeLast('.', name)
        val m = Regex("^(.+?)\\s*-\\s*(\\d{4})$").find(base)
        if (m != null) base = m.groupValues[1]
        return base.trim()
    }

    /**
     * Multi-select import (v1.16): ONE shared-year dialog for the whole
     * selection — each file keeps its own name as its title — then the
     * files import sequentially on a background thread (the server
     * assigns the next number per call, so they number in turn). The
     * year is prefilled when every selected name parses to the same
     * trailing year. Ends with a summary ("Imported X of N", naming
     * any failures), a reload of the tab's list, and the browser closed.
     */
    private fun showDropboxMultiImport(type: String, selected: List<Api.DbxEntry>, browserDialog: AlertDialog) {
        val years = selected.map { dbxYearOf(it.name) }
        val prefill = if (years.all { it != null } && years.distinct().size == 1) years.first() ?: "" else ""
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(8), dp(20), 0)
        }
        col.addView(TextView(this).apply {
            text = "${selected.size} files selected. Each file keeps its own name as its title — give them all the same year:"
            textSize = 13f; setTextColor(SUB)
        })
        for (e in selected) {
            col.addView(TextView(this).apply {
                text = "• ${dbxTitleOf(e.name)}"
                textSize = 13f; setTextColor(SUB)
                setPadding(dp(8), dp(2), 0, 0)
            })
        }
        val yearF = field("Year (4 digits)").apply {
            setText(prefill)
            inputType = android.text.InputType.TYPE_CLASS_NUMBER
        }
        col.addView(yearF)
        val yearDialog = AlertDialog.Builder(this)
            .setTitle("Year for these files")
            .setView(col)
            .setPositiveButton("Import", null)
            .setNegativeButton("Cancel", null)
            .create()
        yearDialog.show()
        yearDialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
            val y = yearF.text.toString().trim()
            if (!Regex("^\\d{4}$").matches(y)) {
                yearF.error = "Enter a 4-digit year"
                return@setOnClickListener
            }
            yearDialog.dismiss()
            val progressText = TextView(this).apply {
                text = "Importing 1 of ${selected.size}…"
                textSize = 14f; setTextColor(TEXT)
                setPadding(dp(24), dp(20), dp(24), dp(12))
            }
            val progressDialog = AlertDialog.Builder(this)
                .setTitle("Importing from Dropbox")
                .setView(progressText)
                .setCancelable(false)
                .create()
            progressDialog.show()
            thread {
                var ok = 0
                val failures = ArrayList<String>()
                for ((i, e) in selected.withIndex()) {
                    post { progressText.text = "Importing ${i + 1} of ${selected.size}…" }
                    try {
                        Api.dropboxImport(this, e.path, type, dbxTitleOf(e.name), y)
                        ok++
                    } catch (ex: Exception) {
                        Diag.record(this, "dropboxImport multi", ex)
                        failures.add(e.name)
                    }
                }
                post {
                    progressDialog.dismiss()
                    try { browserDialog.dismiss() } catch (ignored: Exception) { }
                    refresh(type, keepOrder = false)
                    if (failures.isEmpty()) {
                        toast("Imported $ok of ${selected.size}")
                    } else {
                        AlertDialog.Builder(this)
                            .setTitle("Dropbox import")
                            .setMessage("Imported $ok of ${selected.size}.\n\nFailed:\n" + failures.joinToString("\n") { "• $it" })
                            .setPositiveButton("OK", null)
                            .show()
                    }
                }
            }
        }
    }

    private fun showDropboxNameDialog(type: String, entry: Api.DbxEntry) {
        var preTitle = entry.name.substringBeforeLast('.', entry.name)
        var preYear = ""
        val m = Regex("^(.+?)\\s*-\\s*(\\d{4})$").find(preTitle)
        if (m != null) { preTitle = m.groupValues[1]; preYear = m.groupValues[2] }
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(8), dp(20), 0)
        }
        col.addView(TextView(this).apply { text = "“${entry.name}” (${fmtSize(entry.size)}) will be copied from Dropbox as the next ${kindLower(type)}."; textSize = 13f; setTextColor(SUB) })
        val titleF = field("Title").apply { setText(preTitle) }
        val yearF = field("Year (4 digits)").apply {
            setText(preYear)
            inputType = android.text.InputType.TYPE_CLASS_NUMBER
        }
        col.addView(titleF)
        col.addView(yearF)
        AlertDialog.Builder(this)
            .setTitle("Name this ${kindLower(type)}")
            .setView(col)
            .setPositiveButton("Import") { _, _ ->
                val t = titleF.text.toString().trim()
                val y = yearF.text.toString().trim()
                if (t.isEmpty() || !Regex("^\\d{4}$").matches(y)) {
                    toast("A title and a 4-digit year are needed.")
                    return@setPositiveButton
                }
                showProgress("Importing", "The server is copying the file from Dropbox…", indeterminate = true)
                thread {
                    try {
                        val key = Api.dropboxImport(this, entry.path, type, t, y)
                        post { hideProgress(); toast("Imported: ${key.substringAfterLast('/')}" + if (type == "videos") " · poster comes from the sweep" else ""); refresh(type, keepOrder = false) }
                    } catch (e: Exception) {
                        Diag.record(this, "dropboxImport", e)
                        post { hideProgress(); toast("Import failed: ${e.message}") }
                    }
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    // ------------------------------------------------------------ link import

    private fun showImportUrlDialog(type: String) {
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(8), dp(20), 0)
        }
        col.addView(TextView(this).apply {
            text = "Paste a link to the file — a Dropbox share link works (the server fetches it directly, so a large file never passes through the phone)."
            textSize = 13f; setTextColor(SUB)
        })
        val urlF = field("https://…")
        val titleF = field("Title")
        val yearF = field("Year (4 digits)").apply { inputType = android.text.InputType.TYPE_CLASS_NUMBER }
        col.addView(urlF); col.addView(titleF); col.addView(yearF)
        AlertDialog.Builder(this)
            .setTitle("Import a ${kindLower(type)} from a link")
            .setView(col)
            .setPositiveButton("Import") { _, _ ->
                val u = urlF.text.toString().trim()
                val t = titleF.text.toString().trim()
                val y = yearF.text.toString().trim()
                if (u.isEmpty() || t.isEmpty() || !Regex("^\\d{4}$").matches(y)) {
                    toast("The link, a title, and a 4-digit year are all needed.")
                    return@setPositiveButton
                }
                showProgress("Importing", "The server is fetching the file…", indeterminate = true)
                thread {
                    try {
                        val key = Api.importUrl(this, u, type, t, y)
                        val posterOk = if (type == "videos") uploadPoster(key, posterFromUrl(u)) else false
                        post { hideProgress(); toast("Imported: ${key.substringAfterLast('/')}" + if (type == "videos") (if (posterOk) " · poster added" else " · poster comes from the sweep") else ""); refresh(type, keepOrder = false) }
                    } catch (e: Exception) {
                        Diag.record(this, "importUrl", e)
                        post { hideProgress(); toast("Import failed: ${e.message}") }
                    }
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    // ------------------------------------------------------------ WebDAV relay

    private fun showWebDavDialog(type: String) {
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(8), dp(20), 0)
        }
        col.addView(TextView(this).apply {
            text = "Enter the WebDAV address of the file itself. The phone downloads it, then uploads it to the page. Username and password are optional."
            textSize = 13f; setTextColor(SUB)
        })
        val urlF = field("https://server/…/file")
        navWebDavAddress().takeIf { it.isNotEmpty() }?.let { urlF.setText(it) }
        val userF = field("Username (optional)")
        val passF = field("Password (optional)", android.text.InputType.TYPE_CLASS_TEXT or android.text.InputType.TYPE_TEXT_VARIATION_PASSWORD)
        col.addView(urlF); col.addView(userF); col.addView(passF)
        AlertDialog.Builder(this)
            .setTitle("From Network Share")
            .setView(col)
            .setPositiveButton("Download") { _, _ ->
                val u = urlF.text.toString().trim()
                if (u.isEmpty()) { toast("The file address is needed."); return@setPositiveButton }
                navSaveWebDavAddress(u)
                val auth = if (userF.text.toString().isNotEmpty()) {
                    "Basic " + Base64.encodeToString("${userF.text}:${passF.text}".toByteArray(Charsets.UTF_8), Base64.NO_WRAP)
                } else null
                val name = u.substringAfterLast('/').substringBefore('?').ifEmpty { "download" }
                showProgress("Downloading", name, indeterminate = false)
                thread {
                    val dest = File(cacheDir, "stage").apply { mkdirs() }
                    val f = File(dest, "dav-" + name.replace(Regex("[^A-Za-z0-9._-]"), "_"))
                    try {
                        Api.downloadToCache(this, u, auth, f) { got, total ->
                            post { setProgress(if (total > 0) (got * 1000 / total).toInt() else 0, "${fmtSize(got)} of ${fmtSize(total)}") }
                        }
                        val fresh = try { Api.list(this, type) } catch (e: Exception) { lists[type] ?: emptyList() }
                        standaloneNext = nextNumber(fresh)
                        post {
                            hideProgress()
                            askTitleYearThenUpload(type, f, name) { success ->
                                f.delete()
                                if (success) refresh(type, keepOrder = false)
                            }
                        }
                    } catch (e: Exception) {
                        Diag.record(this, "webdav", e)
                        post { hideProgress(); toast("Download failed: ${e.message}") }
                    }
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    // ------------------------------------------------------------ guided sources

    private fun showGuidedDialog(source: String) {
        val (title, body) = when (source) {
            "amazon" -> "From Amazon Photos" to
                "Amazon Photos does not let apps pull from your library directly, so the working path is two steps:\n\n1. In the Amazon Photos app or on its website, download the items you want onto this phone.\n\n2. Come back here and pick them with the Files picker — they upload like any other phone file."
            "facebook" -> "From Facebook" to
                "Facebook does not let apps pull your photos and videos directly. The sanctioned path is Facebook's own export:\n\n1. In Facebook: Settings → Download Your Information → request your Photos and Videos.\n\n2. When the export is ready, download the ZIP to this phone and unzip it (open it in the Files app).\n\n3. Come back here and pick the media files from the unzipped folders."
            else -> "From Instagram" to
                "Instagram does not let apps pull your media directly. The sanctioned path is Instagram's own export:\n\n1. In Instagram: Accounts Center → Your information and permissions → Download your information → request your media.\n\n2. When the export is ready, download the ZIP to this phone and unzip it (open it in the Files app).\n\n3. Come back here and pick the media files from the unzipped folders."
        }
        AlertDialog.Builder(this)
            .setTitle(title)
            .setMessage(body)
            .setPositiveButton("Open Files") { _, _ -> launchFiles(pendingType, exportMode = true) }
            .setNegativeButton("Close", null)
            .show()
    }

    // ------------------------------------------------------------ links catalog
    // The Links tab (v1.10). The catalog starts COMPLETELY empty and
    // there is no predefined category list anywhere: categories and
    // subcategories exist only because links were placed under them.
    // The pickers below therefore offer only what the loaded catalog
    // already uses, plus "New category…".
    // v1.12: the list is a RecyclerView (LinksAdapter) like the media
    // tabs, and link rows carry a ≡ drag handle — drag reorders
    // within the link's own group only and, like ▲/▼, is local until
    // Save Order.

    private fun refreshLinks() {
        setStatus("Loading links…", "links")
        thread {
            try {
                val (cats, items) = Api.linksList(this)
                post {
                    linksCategories = cats
                    linksItems.clear()
                    linksItems.addAll(items)
                    linksLoaded = true
                    // A reload replaces the local state wholesale: any
                    // unsaved local order no longer describes the list,
                    // and a selection may point at a link that is gone.
                    linksDirtyGroups.clear()
                    if (selectedLinkId != null && items.none { it.id == selectedLinkId }) selectedLinkId = null
                    // v1.17: prune move-checkbox ids that no longer
                    // exist, for the same stale-key reason as media.
                    linkMoveSelection.retainAll(items.map { it.id }.toSet())
                    setStatus("${items.size} links in the catalog, grouped by category.", "links")
                    if (currentType == "links") {
                        renderLinks()
                        updateSaveOrder()
                    }
                }
            } catch (e: Exception) {
                Diag.record(this, "linksList", e)
                post { setStatus("Could not load links: ${e.message}", "links") }
            }
        }
    }

    /** Categories already in use, first-appearance order (server list first). */
    private fun existingCats(): List<String> {
        val out = ArrayList<String>()
        for (c in linksCategories) if (c.isNotEmpty() && c !in out) out.add(c)
        for (l in linksItems) if (l.cat.isNotEmpty() && l.cat !in out) out.add(l.cat)
        return out
    }

    /** Subcategories already in use under one category, first-appearance order. */
    private fun existingSubs(cat: String): List<String> {
        val out = ArrayList<String>()
        for (l in linksItems) if (l.cat == cat && l.sub.isNotEmpty() && l.sub !in out) out.add(l.sub)
        return out
    }

    /** (category, subcategory) groups in first-appearance order. */
    private fun linkGroups(): List<Triple<String, String, List<Api.LinkItem>>> {
        val map = LinkedHashMap<Pair<String, String>, MutableList<Api.LinkItem>>()
        for (l in linksItems) map.getOrPut(l.cat to l.sub) { ArrayList() }.add(l)
        return map.map { (k, v) -> Triple(k.first, k.second, v.toList()) }
    }

    /**
     * One row of the Links RecyclerView (v1.12): a group header, a
     * link entry (displayIdx/displaySize are the link's place within
     * the DISPLAYED group, which a search may have narrowed), or a
     * plain message line for the loading / empty / no-match states.
     */
    sealed class LinkRow {
        data class Header(val cat: String, val sub: String) : LinkRow()
        data class Entry(val item: Api.LinkItem, val displayIdx: Int, val displaySize: Int) : LinkRow()
        data class Message(val text: String) : LinkRow()
    }

    /**
     * The Links display rows for the current catalog + search. The
     * tab's search narrows the display only: a group shows just its
     * matching links, and a group with no matches is not shown at
     * all. The underlying catalog order is untouched.
     */
    private fun visibleLinkRows(): MutableList<LinkRow> {
        if (!linksLoaded) return mutableListOf(LinkRow.Message("Loading links\u2026"))
        if (linksItems.isEmpty()) return mutableListOf(LinkRow.Message("No links yet \u2014 tap Add Link to add the first one. Categories and subcategories are created as you place links under them."))
        val q = (searchQueries["links"] ?: "").trim().lowercase()
        val rows = ArrayList<LinkRow>()
        var shown = 0
        for ((cat, sub, group) in linkGroups()) {
            val visible = if (q.isEmpty()) group else group.filter { l ->
                l.name.lowercase().contains(q) || l.url.lowercase().contains(q) ||
                    l.cat.lowercase().contains(q) || l.sub.lowercase().contains(q)
            }
            if (visible.isEmpty()) continue
            shown += visible.size
            rows.add(LinkRow.Header(cat, sub))
            visible.forEachIndexed { idx, item -> rows.add(LinkRow.Entry(item, idx, visible.size)) }
        }
        if (shown == 0) {
            return mutableListOf(LinkRow.Message("No links match \u201C${searchQueries["links"]?.trim()}\u201D."))
        }
        return rows
    }

    private fun renderLinks() {
        if (::linksAdapter.isInitialized) {
            linksAdapter.rows = visibleLinkRows()
            linksAdapter.notifyDataSetChanged()
        }
        updateRename()
    }

    private fun smallButton(text: String): Button = Button(this).apply {
        this.text = text
        textSize = 12f
        setTextColor(TEXT)
        background = roundedBg(BG)
        minWidth = 0
        minimumWidth = 0
        setPadding(dp(7), 0, dp(7), 0)
        layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, dp(40)).apply { marginStart = dp(4) }
    }

    /**
     * The Links list adapter (v1.12), mirroring MediaAdapter: group
     * headers and message lines are plain rows, link rows carry the
     * \u2261 drag handle at the left edge. The drag itself is driven by
     * the ItemTouchHelper attached in showLibrary \u2014 it starts from
     * the handle only (long-press drag is disabled), stays inside
     * the dragged link's own group, and is off while a search is
     * filtering. Everything else about a row is the v1.11 behavior:
     * tap selects (gold outline) for the Rename / Move buttons,
     * favicon + name + "N of M \u00B7 host", \u25B2/\u25BC local moves,
     * \u2197 opens, \u2715 deletes with confirm.
     */
    inner class LinksAdapter : RecyclerView.Adapter<RecyclerView.ViewHolder>() {
        var touchHelper: ItemTouchHelper? = null
        var rows: MutableList<LinkRow> = ArrayList()

        inner class HeaderHolder(v: View) : RecyclerView.ViewHolder(v) {
            val text: TextView = v as TextView
        }

        inner class MessageHolder(v: View) : RecyclerView.ViewHolder(v) {
            val text: TextView = v as TextView
        }

        inner class EntryHolder(v: View) : RecyclerView.ViewHolder(v) {
            val moveCheck: CheckBox = v.findViewById(20)
            val handle: TextView = v.findViewById(11)
            val glyph: TextView = v.findViewById(12)
            val favicon: ImageView = v.findViewById(13)
            val name: TextView = v.findViewById(14)
            val sub: TextView = v.findViewById(15)
            val up: Button = v.findViewById(16)
            val dn: Button = v.findViewById(17)
            val open: Button = v.findViewById(18)
            val del: Button = v.findViewById(19)
        }

        override fun getItemViewType(position: Int): Int = when (rows[position]) {
            is LinkRow.Header -> 0
            is LinkRow.Entry -> 1
            is LinkRow.Message -> 2
        }

        override fun getItemCount(): Int = rows.size

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): RecyclerView.ViewHolder {
            return when (viewType) {
                0 -> HeaderHolder(TextView(this@MainActivity).apply {
                    textSize = 15f; setTextColor(GOLD)
                    setPadding(0, dp(12), 0, dp(6))
                    layoutParams = RecyclerView.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
                })
                2 -> MessageHolder(TextView(this@MainActivity).apply {
                    textSize = 13.5f; setTextColor(SUB)
                    layoutParams = RecyclerView.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
                })
                else -> {
                    val row = LinearLayout(this@MainActivity).apply {
                        orientation = LinearLayout.HORIZONTAL
                        gravity = Gravity.CENTER_VERTICAL
                        setPadding(dp(10), dp(8), dp(6), dp(8))
                        layoutParams = RecyclerView.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { bottomMargin = dp(6) }
                    }
                    val handle = TextView(this@MainActivity).apply {
                        id = 11; text = "\u2261"; textSize = 22f; setTextColor(SUB); gravity = Gravity.CENTER
                        setPadding(dp(2), dp(8), dp(8), dp(8))
                    }
                    row.addView(handle)
                    // Multi-select move checkbox (v1.17): a clickable
                    // child of the row, placed after the ≡ handle so
                    // the handle stays at the left edge. A tap on it is
                    // consumed by the checkbox, so it never fires the
                    // row's selection tap and never starts a drag
                    // (drag starts only from the handle's listener).
                    row.addView(CheckBox(this@MainActivity).apply { id = 20 })
                    val frame = FrameLayout(this@MainActivity).apply {
                        layoutParams = LinearLayout.LayoutParams(dp(34), dp(34)).apply { marginEnd = dp(10) }
                        setBackgroundColor(BG)
                    }
                    val glyph = TextView(this@MainActivity).apply {
                        id = 12; text = "\uD83D\uDD17"; textSize = 15f; gravity = Gravity.CENTER
                    }
                    frame.addView(glyph, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
                    val img = ImageView(this@MainActivity).apply { id = 13; scaleType = ImageView.ScaleType.CENTER_INSIDE }
                    frame.addView(img, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
                    row.addView(frame)
                    val texts = LinearLayout(this@MainActivity).apply {
                        orientation = LinearLayout.VERTICAL
                        layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
                    }
                    texts.addView(TextView(this@MainActivity).apply { id = 14; textSize = 14.5f; setTextColor(TEXT) })
                    texts.addView(TextView(this@MainActivity).apply {
                        id = 15; textSize = 12f; setTextColor(SUB)
                        maxLines = 1; ellipsize = TextUtils.TruncateAt.END
                    })
                    row.addView(texts)
                    val up = smallButton("\u25B2"); up.id = 16; row.addView(up)
                    val dn = smallButton("\u25BC"); dn.id = 17; row.addView(dn)
                    val open = smallButton("\u2197"); open.id = 18; row.addView(open)
                    val del = Button(this@MainActivity).apply {
                        id = 19; text = "\u2715"; textSize = 14f; setTextColor(Color.rgb(255, 154, 138)); setBackgroundColor(Color.TRANSPARENT)
                    }
                    row.addView(del, LinearLayout.LayoutParams(dp(40), dp(44)))
                    EntryHolder(row)
                }
            }
        }

        override fun onBindViewHolder(holder: RecyclerView.ViewHolder, position: Int) {
            when (val row = rows[position]) {
                is LinkRow.Header -> (holder as HeaderHolder).text.text =
                    if (row.sub.isEmpty()) row.cat else "${row.cat} \u203A ${row.sub}"
                is LinkRow.Message -> (holder as MessageHolder).text.text = row.text
                is LinkRow.Entry -> bindEntry(holder as EntryHolder, row)
            }
        }

        private fun bindEntry(h: EntryHolder, row: LinkRow.Entry) {
            val item = row.item
            // Move checkbox (v1.17): listener cleared before isChecked
            // is set so a recycled holder never fires for the previous
            // link; toggling touches ONLY the move set and the Move
            // button — never the row selection.
            h.moveCheck.setOnCheckedChangeListener(null)
            h.moveCheck.isChecked = linkMoveSelection.contains(item.id)
            h.moveCheck.setOnCheckedChangeListener { _, checked ->
                if (checked) linkMoveSelection.add(item.id) else linkMoveSelection.remove(item.id)
                updateMoveButton()
            }
            h.itemView.background = if (item.id == selectedLinkId) selectedBg() else roundedBg(CARD)
            // Tap selects (the media tabs' behavior) for the Rename /
            // Move buttons in the actions row; opening lives on the
            // \u2197 button below so the two never conflict.
            h.itemView.setOnClickListener {
                selectedLinkId = if (selectedLinkId == item.id) null else item.id
                notifyDataSetChanged()
                updateRename()
            }
            h.handle.setOnTouchListener { _, ev ->
                if (ev.actionMasked == MotionEvent.ACTION_DOWN) touchHelper?.startDrag(h)
                false
            }
            val host = try { URL(item.url).host ?: "" } catch (e: Exception) { "" }
            h.glyph.visibility = View.VISIBLE
            h.favicon.setImageBitmap(null)
            if (host.isNotEmpty()) loadFavicon(host, h.favicon, h.glyph)
            h.name.text = item.name
            // The Update page's sub-line: place within the group, then the host.
            h.sub.text = "${row.displayIdx + 1} of ${row.displaySize}" + if (host.isNotEmpty()) " \u00B7 $host" else ""
            // \u25B2/\u25BC bounds follow the FULL group, not the filtered
            // display: a move always swaps with the true neighbor in
            // the catalog.
            val fullGroup = linksItems.filter { it.cat == item.cat && it.sub == item.sub }
            val fullIdx = fullGroup.indexOfFirst { it.id == item.id }
            h.up.isEnabled = fullIdx > 0
            h.up.alpha = if (fullIdx > 0) 1f else 0.35f
            h.up.setOnClickListener { moveLinkWithinGroup(item, -1) }
            h.dn.isEnabled = fullIdx >= 0 && fullIdx < fullGroup.size - 1
            h.dn.alpha = if (h.dn.isEnabled) 1f else 0.35f
            h.dn.setOnClickListener { moveLinkWithinGroup(item, 1) }
            h.open.setOnClickListener {
                try {
                    startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(item.url)))
                } catch (e: Exception) {
                    toast("Could not open that link.")
                }
            }
            h.del.setOnClickListener { confirmDeleteLink(item) }
        }
    }

    /**
     * One drag step (v1.12): swap two links of the SAME group in the
     * catalog's local order and mark the group dirty \u2014 the same
     * local-only trail as \u25B2/\u25BC, so nothing reaches the server
     * until Save Order. The helper refuses cross-group targets before
     * this is ever called; the group check here is a second lock.
     */
    private fun swapLinksForDrag(a: Api.LinkItem, b: Api.LinkItem) {
        if (a.cat != b.cat || a.sub != b.sub) return
        val group = linksItems.filter { it.cat == a.cat && it.sub == a.sub }
        val ids = group.map { it.id }.toMutableList()
        val ia = ids.indexOf(a.id)
        val ib = ids.indexOf(b.id)
        if (ia < 0 || ib < 0 || ia == ib) return
        Collections.swap(ids, ia, ib)
        applyGroupOrder(a.cat, a.sub, ids)
        linksDirtyGroups.add(a.cat to a.sub)
        linksDragMoved = true
    }

    /** Google s2 favicon for the row, cached; the 🔗 glyph stays on any failure. */
    private fun loadFavicon(host: String, img: ImageView, glyph: TextView) {
        img.tag = host
        val cached = synchronized(faviconCache) { faviconCache[host] }
        if (cached != null) {
            img.setImageBitmap(cached)
            glyph.visibility = View.GONE
            return
        }
        synchronized(faviconLoading) { if (!faviconLoading.add(host)) return }
        faviconPool.execute {
            try {
                val conn = URL("https://www.google.com/s2/favicons?domain=$host&sz=64").openConnection() as HttpURLConnection
                conn.connectTimeout = 10_000
                conn.readTimeout = 10_000
                val bytes = conn.inputStream.use { it.readBytes() }
                val bmp = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                if (bmp != null) {
                    synchronized(faviconCache) { faviconCache[host] = bmp }
                    post {
                        if (img.tag == host) {
                            img.setImageBitmap(bmp)
                            glyph.visibility = View.GONE
                        }
                    }
                }
            } catch (e: Exception) {
                // Decoration only — the glyph fallback stays.
            } finally {
                synchronized(faviconLoading) { faviconLoading.remove(host) }
            }
        }
    }

    /**
     * ▲/▼ on a link row (v1.11): the swap happens in the LOCAL list
     * only and the group is marked dirty — nothing reaches the server
     * until Save Order, exactly like the media tabs' drag reorder.
     * The swap is computed on the full group, never the filtered
     * display, so a search can never corrupt the saved order.
     */
    private fun moveLinkWithinGroup(item: Api.LinkItem, dir: Int) {
        val group = linksItems.filter { it.cat == item.cat && it.sub == item.sub }
        val idx = group.indexOfFirst { it.id == item.id }
        val swap = idx + dir
        if (idx < 0 || swap < 0 || swap >= group.size) return
        val ids = group.map { it.id }.toMutableList()
        Collections.swap(ids, idx, swap)
        applyGroupOrder(item.cat, item.sub, ids)
        linksDirtyGroups.add(item.cat to item.sub)
        renderLinks()
        updateSaveOrder()
        setStatus("Order changed — tap Save Order to save it.", "links")
    }

    /**
     * Save Order on the Links tab: one /media-admin/links/reorder
     * call per dirty group, with that group's ids in their new local
     * order (the server requires the ids to be exactly the group's
     * membership). A failure keeps the dirty marks and names the
     * error in the tab's status, so nothing is silently half-saved
     * without saying so.
     */
    private fun saveLinksOrder() {
        if (linksDirtyGroups.isEmpty()) return
        val groups = linksDirtyGroups.toList()
        setStatus("Saving the new order…", "links")
        thread {
            try {
                for ((cat, sub) in groups) {
                    val ids = linksItems.filter { it.cat == cat && it.sub == sub }.map { it.id }
                    if (ids.isNotEmpty()) Api.linksReorder(this, cat, sub, ids)
                }
                post {
                    linksDirtyGroups.removeAll(groups.toSet())
                    renderLinks()
                    updateSaveOrder()
                    setStatus("Order saved.", "links")
                    toast("Order saved.")
                }
            } catch (e: Exception) {
                Diag.record(this, "linksSaveOrder", e)
                post { setStatus("Saving the order failed: ${e.message}", "links") }
            }
        }
    }

    private fun applyGroupOrder(cat: String, sub: String, ids: List<String>) {
        val byId = linksItems.associateBy { it.id }
        val groupItems = ids.mapNotNull { byId[it] }
        val firstIdx = linksItems.indexOfFirst { it.cat == cat && it.sub == sub }
        if (firstIdx < 0) return
        linksItems.removeAll { it.cat == cat && it.sub == sub }
        linksItems.addAll(firstIdx.coerceAtMost(linksItems.size), groupItems)
    }

    private data class PickerRefs(val spinner: Spinner, val newCat: EditText, val sub: AutoCompleteTextView)

    private fun darkSpinnerAdapter(items: List<String>): ArrayAdapter<String> {
        return object : ArrayAdapter<String>(this, android.R.layout.simple_spinner_item, items) {
            override fun getView(position: Int, convertView: View?, parent: ViewGroup): View {
                val v = super.getView(position, convertView, parent) as TextView
                v.setTextColor(TEXT)
                v.textSize = 15f
                v.setPadding(dp(10), dp(8), dp(10), dp(8))
                return v
            }
            override fun getDropDownView(position: Int, convertView: View?, parent: ViewGroup): View {
                val v = super.getDropDownView(position, convertView, parent) as TextView
                v.setTextColor(TEXT)
                v.textSize = 15f
                v.setBackgroundColor(CARD)
                v.setPadding(dp(10), dp(10), dp(10), dp(10))
                return v
            }
        }
    }

    /**
     * Appends the Category + Subcategory fields to a dialog column.
     * The category spinner lists ONLY the categories already in the
     * catalog plus "New category…" (which reveals a text field) — with
     * an empty catalog that is the only entry. The subcategory field is
     * free text with suggestions drawn only from the subs already used
     * under the chosen category.
     */
    private fun addCatSubFields(col: LinearLayout, currentCat: String, currentSub: String): PickerRefs {
        val cats = existingCats()
        val entries = cats + NEW_CATEGORY_LABEL
        col.addView(label("Category", 13f, SUB).apply { setPadding(0, dp(10), 0, dp(2)) })
        val spinner = Spinner(this).apply {
            adapter = darkSpinnerAdapter(entries)
            background = roundedBg(CARD)
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
        }
        col.addView(spinner)
        val newCat = field("New category name")
        col.addView(newCat)
        col.addView(label("Subcategory (optional)", 13f, SUB).apply { setPadding(0, dp(10), 0, dp(2)) })
        val sub = AutoCompleteTextView(this).apply {
            hint = "Subcategory"
            setTextColor(TEXT)
            setHintTextColor(SUB)
            textSize = 15f
            setPadding(dp(10), dp(8), dp(10), dp(8))
            threshold = 1
            setText(currentSub)
        }
        col.addView(sub)
        fun refreshSubs() {
            val sel = spinner.selectedItem?.toString() ?: ""
            val subs = if (sel == NEW_CATEGORY_LABEL) emptyList() else existingSubs(sel)
            sub.setAdapter(ArrayAdapter(this, android.R.layout.simple_dropdown_item_1line, subs))
        }
        spinner.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(parent: AdapterView<*>?, view: View?, pos: Int, id: Long) {
                newCat.visibility = if (entries[pos] == NEW_CATEGORY_LABEL) View.VISIBLE else View.GONE
                refreshSubs()
            }
            override fun onNothingSelected(parent: AdapterView<*>?) { }
        }
        val selIdx = when {
            currentCat.isNotEmpty() && currentCat in cats -> cats.indexOf(currentCat)
            cats.isNotEmpty() && currentCat.isEmpty() -> 0
            else -> entries.size - 1
        }
        if (currentCat.isNotEmpty() && currentCat !in cats) newCat.setText(currentCat)
        spinner.setSelection(selIdx)
        newCat.visibility = if (entries[selIdx] == NEW_CATEGORY_LABEL) View.VISIBLE else View.GONE
        refreshSubs()
        return PickerRefs(spinner, newCat, sub)
    }

    private fun pickerCategory(p: PickerRefs): String {
        val sel = p.spinner.selectedItem?.toString() ?: ""
        return if (sel == NEW_CATEGORY_LABEL) p.newCat.text.toString().trim() else sel
    }

    private fun showAddLinkDialog() {
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(8), dp(20), 0)
        }
        val nameF = field("Name")
        val urlF = field("Address (URL)").apply {
            inputType = android.text.InputType.TYPE_CLASS_TEXT or android.text.InputType.TYPE_TEXT_VARIATION_URI
        }
        col.addView(nameF)
        col.addView(urlF)
        val picker = addCatSubFields(col, "", "")
        AlertDialog.Builder(this)
            .setTitle("Add Link")
            .setView(col)
            .setPositiveButton("Add") { _, _ ->
                val name = nameF.text.toString().trim()
                var url = urlF.text.toString().trim()
                val cat = pickerCategory(picker)
                val sub = picker.sub.text.toString().trim()
                if (name.isEmpty() || url.isEmpty() || cat.isEmpty()) {
                    toast("A name, an address, and a category are all needed.")
                    return@setPositiveButton
                }
                if (!url.contains("://")) url = "https://$url"
                setStatus("Adding link…", "links")
                thread {
                    try {
                        val created = Api.linksAdd(this, name, url, cat, sub)
                        post {
                            linksItems.add(created)
                            if (created.cat.isNotEmpty() && created.cat !in linksCategories) {
                                linksCategories = linksCategories + created.cat
                            }
                            linksLoaded = true
                            renderLinks()
                            setStatus("Added “${created.name}”.", "links")
                            toast("Added: ${created.name}")
                        }
                    } catch (e: Exception) {
                        Diag.record(this, "linksAdd", e)
                        post { setStatus("Add failed: ${e.message}", "links") }
                    }
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun showRenameLinkDialog(item: Api.LinkItem) {
        val input = field("Name").apply { setText(item.name); setSelection(text.length) }
        AlertDialog.Builder(this)
            .setTitle("Rename Link")
            .setMessage("Only the name changes — the address and its category stay the same.")
            .setView(input)
            .setPositiveButton("Rename") { _, _ ->
                val name = input.text.toString().trim()
                if (name.isEmpty() || name == item.name) return@setPositiveButton
                setStatus("Renaming…", "links")
                thread {
                    try {
                        Api.linksRename(this, item.id, name)
                        post {
                            val i = linksItems.indexOfFirst { it.id == item.id }
                            if (i >= 0) linksItems[i] = linksItems[i].copy(name = name)
                            renderLinks()
                            setStatus("Renamed to “$name”.", "links")
                        }
                    } catch (e: Exception) {
                        Diag.record(this, "linksRename", e)
                        post { setStatus("Rename failed: ${e.message}", "links") }
                    }
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun showMoveLinkDialog(item: Api.LinkItem) {
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(8), dp(20), 0)
        }
        col.addView(TextView(this).apply {
            text = "“${item.name}” moves to the end of the chosen category / subcategory group."
            textSize = 13f; setTextColor(SUB)
        })
        val picker = addCatSubFields(col, item.cat, item.sub)
        AlertDialog.Builder(this)
            .setTitle("Move Link")
            .setView(col)
            .setPositiveButton("Move") { _, _ ->
                val cat = pickerCategory(picker)
                val sub = picker.sub.text.toString().trim()
                if (cat.isEmpty()) {
                    toast("A category is needed.")
                    return@setPositiveButton
                }
                if (cat == item.cat && sub == item.sub) return@setPositiveButton
                setStatus("Moving…", "links")
                thread {
                    try {
                        Api.linksMove(this, item.id, cat, sub)
                        post {
                            // The server places it at the END of the target group.
                            val moved = item.copy(cat = cat, sub = sub)
                            linksItems.removeAll { it.id == item.id }
                            val lastIdx = linksItems.indexOfLast { it.cat == cat && it.sub == sub }
                            if (lastIdx >= 0) linksItems.add(lastIdx + 1, moved) else linksItems.add(moved)
                            if (cat.isNotEmpty() && cat !in linksCategories) {
                                linksCategories = linksCategories + cat
                            }
                            renderLinks()
                            setStatus("Moved “${item.name}”.", "links")
                        }
                    } catch (e: Exception) {
                        Diag.record(this, "linksMove", e)
                        post { setStatus("Move failed: ${e.message}", "links") }
                    }
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    /**
     * Bulk link move (v1.17): the single-link dialog's construction
     * (addCatSubFields — Category spinner incl. "New category…" plus
     * the Subcategory field) applied to the whole checkbox set. The
     * moves are server-side immediate: Api.linksMove once per selected
     * link, sequentially on a background thread, in catalog order.
     * On success the links are reloaded and the set cleared; on the
     * first failure the loop stops, the catalog is still reloaded (so
     * the display matches the server, including the links that DID
     * move), the set is cleared, and the status/toast say how many
     * moved and that the rest did not. No Save Order is involved.
     */
    private fun showBulkMoveLinksDialog() {
        // Catalog order, not check order, so the sequential moves (and
        // their end-of-group placements) are deterministic.
        val selected = linksItems.filter { it.id in linkMoveSelection }
        if (selected.isEmpty()) {
            linkMoveSelection.clear()
            updateMoveButton()
            return
        }
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(8), dp(20), 0)
        }
        col.addView(TextView(this).apply {
            text = "${selected.size} links move to the end of the chosen category / subcategory group."
            textSize = 13f; setTextColor(SUB)
        })
        // Prefill the picker only when the whole set shares one
        // category/subcategory; a mixed set starts from the catalog's
        // first category, like an add.
        val sharedCat = if (selected.distinctBy { it.cat }.size == 1) selected.first().cat else ""
        val sharedSub = if (selected.distinctBy { it.sub }.size == 1) selected.first().sub else ""
        val picker = addCatSubFields(col, sharedCat, sharedSub)
        AlertDialog.Builder(this)
            .setTitle("Move ${selected.size} links")
            .setView(col)
            .setPositiveButton("Move") { _, _ ->
                val cat = pickerCategory(picker)
                val sub = picker.sub.text.toString().trim()
                if (cat.isEmpty()) {
                    toast("A category is needed.")
                    return@setPositiveButton
                }
                if (selected.all { it.cat == cat && it.sub == sub }) return@setPositiveButton
                val dest = if (sub.isEmpty()) cat else "$cat \u203A $sub"
                setStatus("Moving\u2026", "links")
                thread {
                    var moved = 0
                    var failure: Exception? = null
                    for (link in selected) {
                        try {
                            Api.linksMove(this, link.id, cat, sub)
                            moved++
                        } catch (e: Exception) {
                            Diag.record(this, "linksMove bulk", e)
                            failure = e
                            break
                        }
                    }
                    // Reload in this same thread (mirroring
                    // refreshLinks' apply block) so the final status
                    // below is the one that sticks — refreshLinks
                    // would overwrite it with its own load status.
                    val reloaded = try { Api.linksList(this) } catch (e: Exception) { null }
                    val err = failure
                    val movedCount = moved
                    post {
                        linkMoveSelection.clear()
                        if (reloaded != null) {
                            val (cats, items) = reloaded
                            linksCategories = cats
                            linksItems.clear()
                            linksItems.addAll(items)
                            linksLoaded = true
                            linksDirtyGroups.clear()
                            if (selectedLinkId != null && items.none { it.id == selectedLinkId }) selectedLinkId = null
                        }
                        if (currentType == "links") {
                            renderLinks()
                            updateSaveOrder()
                        }
                        updateMoveButton()
                        if (err == null) {
                            setStatus("Moved $movedCount links to $dest", "links")
                            toast("Moved $movedCount links to $dest")
                        } else {
                            setStatus("Moved $movedCount of ${selected.size} links to $dest \u2014 the rest did not move: ${err.message}", "links")
                            toast("Moved $movedCount of ${selected.size} links \u2014 the rest did not move.")
                        }
                    }
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun confirmDeleteLink(item: Api.LinkItem) {
        AlertDialog.Builder(this)
            .setTitle("Delete this link?")
            .setMessage("“${item.name}” will be removed from the links catalog. This cannot be undone from the app.")
            .setPositiveButton("Delete") { _, _ ->
                setStatus("Deleting…", "links")
                thread {
                    try {
                        Api.linksDelete(this, item.id)
                        post {
                            linksItems.removeAll { it.id == item.id }
                            if (selectedLinkId == item.id) selectedLinkId = null
                            linkMoveSelection.remove(item.id)
                            renderLinks()
                            setStatus("Deleted “${item.name}”.", "links")
                        }
                    } catch (e: Exception) {
                        Diag.record(this, "linksDelete", e)
                        post { setStatus("Delete failed: ${e.message}", "links") }
                    }
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    // ------------------------------------------------------------ self-update
    // The two-door pattern shared with the other estate apps (Luddy,
    // 2026-11-05): the ljm3.me door rides behind the saved dashboard
    // sign-in and goes first; the public software site is the fallback
    // door. Every door's failure is recorded separately so a failure
    // report shows the real cause, never just the last one.

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
                        conn.setRequestProperty("User-Agent", "MediaManager/" + BuildConfig.VERSION_NAME)
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
                val apk = info.optString("apk", "media-manager.apk")
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
                cm?.setPrimaryClip(android.content.ClipData.newPlainText("Media Manager update check failure", detail))
                toast("Copied — paste it to JMan.")
            }
            .setNegativeButton("Close", null)
            .show()
    }

    private fun promptUpdate(versionName: String, apk: String, notes: String) {
        AlertDialog.Builder(this)
            .setTitle("Update available")
            .setMessage("LJM3 Media Manager $versionName is available." +
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
                "$BASE/mediamanager/$apk" to basic,
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
                    conn.setRequestProperty("User-Agent", "MediaManager/" + BuildConfig.VERSION_NAME)
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
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.O &&
            !packageManager.canRequestPackageInstalls()
        ) {
            toast("Allow installs from this app to finish the update.")
            startActivity(
                Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:$packageName"))
            )
            return
        }
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
