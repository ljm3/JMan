package net.aniello.mediamanager

import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Color
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.OpenableColumns
import android.provider.Settings
import android.util.Base64
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ProgressBar
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
 * media-admin API: browse photos and videos in their live order,
 * drag to reorder (the server renames the NNN key prefixes, so the
 * bucket stays the catalog), delete, and add new items from the phone,
 * a web link (Dropbox included), a network share (WebDAV relay), or
 * the guided export paths for Amazon Photos, Facebook and Instagram.
 * Self-update rides ljm3.me behind the sign-in first and
 * software.aniello.net second, like the other estate apps.
 */
class MainActivity : Activity() {

    companion object {
        private const val BASE = "https://ljm3.me"
        private const val VERSION_URL_LJM3 = "$BASE/mediamanager/app-version.json"
        private const val VERSION_URL_SOFTWARE = "https://software.aniello.net/apps/media-manager-version.json"
        private const val APK_BASE_SOFTWARE = "https://software.aniello.net/apps/"
        private const val REQ_GALLERY = 1001
        private const val REQ_FILES = 1002
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
    private lateinit var renameBtn: Button
    private var selectedKey: String? = null
    private val statusByType = HashMap<String, String>()
    private lateinit var saveOrderBtn: Button
    private lateinit var tabPhotos: Button
    private lateinit var tabVideos: Button
    private var lastUpdateCheckMs = 0L
    private var lastPromptedVersionCode = 0L
    private var signedInOnce = false
    private var reorderRunning = false

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
        col.addView(label("Manage the photos and videos on your ljm3.me page. Sign in with your dashboard username and password.", 13.5f, SUB).apply {
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
            refresh(currentType, keepOrder = false)
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
        tabPhotos = darkButton("Photos")
        tabVideos = darkButton("Videos")
        tabPhotos.setOnClickListener { switchTab("photos") }
        tabVideos.setOnClickListener { switchTab("videos") }
        tabs.addView(tabPhotos, LinearLayout.LayoutParams(0, dp(42), 1f).apply { marginEnd = dp(6) })
        tabs.addView(tabVideos, LinearLayout.LayoutParams(0, dp(42), 1f))
        col.addView(tabs)

        val actions = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(dp(14), dp(4), dp(14), dp(4))
        }
        addPhotoBtn = darkButton("Add Photo")
        addVideoBtn = darkButton("Add Video")
        renameBtn = darkButton("Rename Photo")
        saveOrderBtn = goldButton("Save Order")
        addPhotoBtn.setOnClickListener { showSourceSheet("photos") }
        addVideoBtn.setOnClickListener { showSourceSheet("videos") }
        renameBtn.setOnClickListener { showRename() }
        saveOrderBtn.setOnClickListener { saveOrder() }
        actions.addView(addPhotoBtn, LinearLayout.LayoutParams(0, dp(42), 1f).apply { marginEnd = dp(6) })
        actions.addView(addVideoBtn, LinearLayout.LayoutParams(0, dp(42), 1f).apply { marginEnd = dp(6) })
        actions.addView(renameBtn, LinearLayout.LayoutParams(0, dp(42), 1f).apply { marginEnd = dp(6) })
        actions.addView(saveOrderBtn, LinearLayout.LayoutParams(0, dp(42), 1.2f))
        col.addView(actions)

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
                if (from < 0 || to < 0 || from >= items.size || to >= items.size) return false
                Collections.swap(items, from, to)
                adapter.notifyItemMoved(from, to)
                dirty[currentType] = true
                updateSaveOrder()
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
    }

    private fun switchTab(type: String) {
        if (currentType == type) return
        currentType = type
        selectedKey = null
        updateTabs()
        adapter.notifyDataSetChanged()
        updateSaveOrder()
    }

    private fun updateTabs() {
        val on = GOLD; val off = CARD
        tabPhotos.background = roundedBg(if (currentType == "photos") on else off)
        tabPhotos.setTextColor(if (currentType == "photos") Color.rgb(11, 15, 22) else TEXT)
        tabVideos.background = roundedBg(if (currentType == "videos") on else off)
        tabVideos.setTextColor(if (currentType == "videos") Color.rgb(11, 15, 22) else TEXT)
        addPhotoBtn.visibility = if (currentType == "photos") View.VISIBLE else View.GONE
        addVideoBtn.visibility = if (currentType == "videos") View.VISIBLE else View.GONE
        renameBtn.text = if (currentType == "photos") "Rename Photo" else "Rename Video"
        updateRename()
        showStatusForCurrentTab()
    }

    private fun updateRename() {
        val has = selectedKey != null
        renameBtn.isEnabled = has
        renameBtn.alpha = if (has) 1f else 0.45f
    }

    private fun updateSaveOrder() {
        val d = dirty[currentType] == true
        saveOrderBtn.isEnabled = d
        saveOrderBtn.alpha = if (d) 1f else 0.45f
    }

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
                    }
                    if (type == currentType) {
                        adapter.notifyDataSetChanged()
                        updateSaveOrder()
                        swipe.isRefreshing = false
                        setStatus("${lists[type]?.size ?: 0} $type, in the order they appear on the page. Drag the ≡ handle to reorder, then Save Order.", type)
                    }
                }
            } catch (e: Exception) {
                Diag.record(this, "refresh $type", e)
                post {
                    if (type == currentType) {
                        swipe.isRefreshing = false
                        setStatus("Could not load $type: ${e.message}", type)
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
        val items = lists[type]?.toList() ?: return
        if (items.isEmpty()) return
        val targets = items.mapIndexed { i, it -> finalKeyFor(type, i, it.key) }

        // Pure simulation: build the full ordered step list first.
        val cur = items.map { it.key }.toMutableList()
        val occupied = items.map { it.key }.toMutableSet()
        val pending = (0 until items.size).filter { cur[it] != targets[it] }.toMutableSet()
        if (pending.isEmpty()) {
            dirty[type] = false
            updateSaveOrder()
            setStatus("That is already the saved order.", type)
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
                    setStatus("Order saved — the page now shows this order.", type)
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
            .setTitle("Delete this ${if (currentType == "photos") "photo" else "video"}?")
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

    private fun showRename() {
        val type = currentType
        val item = lists[type]?.firstOrNull { it.key == selectedKey } ?: return
        val kind = if (type == "photos") "Photo" else "Video"
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

        fun refreshPositions() {
            // Position numbers are part of the sub-line; simplest correct
            // refresh is a full rebind of visible rows.
            notifyDataSetChanged()
        }

        override fun getItemCount(): Int = lists[currentType]?.size ?: 0

        override fun onBindViewHolder(h: Holder, position: Int) {
            val items = lists[currentType] ?: return
            val item = items[position]
            h.title.text = item.title
            h.sub.text = "${position + 1} of ${items.size} · ${fmtSize(item.size)}"
            h.del.setOnClickListener { confirmDelete(item) }
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
                    val opts = BitmapFactory.Options().apply { inSampleSize = 4 }
                    val bmp = BitmapFactory.decodeByteArray(bytes, 0, bytes.size, opts)
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
    private var progressDialog: AlertDialog? = null
    private var progressBar: ProgressBar? = null
    private var progressText: TextView? = null

    private fun showSourceSheet(type: String) {
        pendingType = type
        val kind = if (type == "photos") "Photo" else "Video"
        val options = arrayOf(
            "From this phone — ${kind}s",
            "From this phone — Files",
            "Browse my Dropbox",
            "From Dropbox or a web link",
            "From a network share (WebDAV)",
            "From Amazon Photos",
            "From Facebook",
            "From Instagram")
        AlertDialog.Builder(this)
            .setTitle("Add a $kind")
            .setItems(options) { _, which ->
                when (which) {
                    0 -> launchGallery(type)
                    1 -> launchFiles(type, exportMode = false)
                    2 -> showDropboxBrowser(type, "")
                    3 -> showImportUrlDialog(type)
                    4 -> showWebDavDialog(type)
                    5 -> showGuidedDialog("amazon")
                    6 -> showGuidedDialog("facebook")
                    7 -> showGuidedDialog("instagram")
                }
            }
            .show()
    }

    private fun launchGallery(type: String) {
        pendingType = type
        val intent = Intent(Intent.ACTION_GET_CONTENT).apply {
            this.type = if (type == "photos") "image/*" else "video/*"
            addCategory(Intent.CATEGORY_OPENABLE)
            putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true)
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
                else -> "video/*"
            }
            putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true)
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
        val allowed = if (type == "photos") setOf("jpg", "jpeg", "png", "webp", "gif") else setOf("mp4", "webm")
        val suffix = name.substringAfterLast('.', "").lowercase()
        if (suffix in allowed) return ".$suffix"
        return when (mime) {
            "image/jpeg" -> ".jpg"
            "image/png" -> ".png"
            "image/webp" -> ".webp"
            "image/gif" -> ".gif"
            "video/mp4" -> ".mp4"
            "video/webm" -> ".webm"
            else -> if (type == "photos") ".jpg" else ".mp4"
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
            post { processQueueItem(type, uris, 0, start) }
        }
    }

    private fun processQueueItem(type: String, uris: List<Uri>, index: Int, nextNum: Int) {
        if (index >= uris.size || queueCancelled) {
            queueActive = false
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
                    askTitleYearThenUpload(type, staged, name) { success ->
                        staged.delete()
                        processQueueItem(type, uris, index + 1, if (success) nextNum + 1 else nextNum)
                    }
                }
            }
        }
    }

    /** Asks Title + Year (prefilled from the file name when it follows the pattern), then uploads the staged file. */
    private fun askTitleYearThenUpload(type: String, file: File, displayName: String, onDone: (Boolean) -> Unit) {
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
        val yearF = field("Year (4 digits)").apply {
            setText(preYear)
            inputType = android.text.InputType.TYPE_CLASS_NUMBER
        }
        col.addView(TextView(this).apply { text = "“$displayName” will be added as the next ${if (type == "photos") "photo" else "video"}. The name follows the page pattern: number - Title - Year."; textSize = 13f; setTextColor(SUB) })
        col.addView(titleF)
        col.addView(yearF)
        AlertDialog.Builder(this)
            .setTitle(if (type == "photos") "Name this photo" else "Name this video")
            .setView(col)
            .setPositiveButton("Upload") { _, _ ->
                val title = titleF.text.toString().trim().replace('/', '-')
                val year = yearF.text.toString().trim()
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
                if (type == "photos") Api.putFile(this, key, file, cb) else Api.multipartUpload(this, key, file, cb)
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

    // ------------------------------------------------------------ Dropbox browse (Stage 2)
    // The server holds the Dropbox sign-in (connected once at
    // ljm3.me/oauth/dropbox); the app only browses and picks. Picking a
    // file asks Title + Year, then the server copies it straight from
    // Dropbox into the library — the file never passes through the phone.

    private fun showDropboxBrowser(type: String, path: String) {
        showProgress("Dropbox", "Loading…", indeterminate = true)
        thread {
            try {
                val entries = Api.dropboxList(this, path)
                post { hideProgress(); showDropboxEntries(type, path, entries) }
            } catch (e: Exception) {
                Diag.record(this, "dropboxList", e)
                post { hideProgress(); toast("Dropbox: ${e.message}") }
            }
        }
    }

    private fun showDropboxEntries(type: String, path: String, entries: List<Api.DbxEntry>) {
        val allowed = if (type == "photos") setOf("jpg", "jpeg", "png", "webp", "gif") else setOf("mp4", "webm")
        val labels = ArrayList<String>()
        val actions = ArrayList<() -> Unit>()
        if (path.isNotEmpty()) {
            labels.add("⬆  Up one level")
            val parent = path.substringBeforeLast('/', "")
            actions.add { showDropboxBrowser(type, parent) }
        }
        var hidden = 0
        for (e in entries) {
            if (e.tag == "folder") {
                labels.add("📁  ${e.name}")
                val p = e.path
                actions.add { showDropboxBrowser(type, p) }
            } else if (e.name.substringAfterLast('.', "").lowercase() in allowed) {
                labels.add("${e.name}  (${fmtSize(e.size)})")
                actions.add { showDropboxNameDialog(type, e) }
            } else hidden++
        }
        if (actions.isEmpty()) {
            labels.add("(nothing here that can be added)")
            actions.add { }
        }
        AlertDialog.Builder(this)
            .setTitle(if (path.isEmpty()) "Dropbox — top level" else "Dropbox — ${path.substringAfterLast('/')}")
            .setItems(labels.toTypedArray()) { _, which -> actions[which]() }
            .setNegativeButton("Close", null)
            .show()
        if (hidden > 0) toast("$hidden file(s) here are not ${if (type == "photos") "photos" else "videos"} and are not shown.")
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
        col.addView(TextView(this).apply { text = "“${entry.name}” (${fmtSize(entry.size)}) will be copied from Dropbox as the next ${if (type == "photos") "photo" else "video"}."; textSize = 13f; setTextColor(SUB) })
        val titleF = field("Title").apply { setText(preTitle) }
        val yearF = field("Year (4 digits)").apply {
            setText(preYear)
            inputType = android.text.InputType.TYPE_CLASS_NUMBER
        }
        col.addView(titleF)
        col.addView(yearF)
        AlertDialog.Builder(this)
            .setTitle(if (type == "photos") "Name this photo" else "Name this video")
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
            text = "Paste a link to the file — a Dropbox share link works (the server fetches it directly, so a large video never passes through the phone)."
            textSize = 13f; setTextColor(SUB)
        })
        val urlF = field("https://…")
        val titleF = field("Title")
        val yearF = field("Year (4 digits)").apply { inputType = android.text.InputType.TYPE_CLASS_NUMBER }
        col.addView(urlF); col.addView(titleF); col.addView(yearF)
        AlertDialog.Builder(this)
            .setTitle(if (type == "photos") "Import a photo from a link" else "Import a video from a link")
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
        val userF = field("Username (optional)")
        val passF = field("Password (optional)", android.text.InputType.TYPE_CLASS_TEXT or android.text.InputType.TYPE_TEXT_VARIATION_PASSWORD)
        col.addView(urlF); col.addView(userF); col.addView(passF)
        AlertDialog.Builder(this)
            .setTitle("From a network share (WebDAV)")
            .setView(col)
            .setPositiveButton("Download") { _, _ ->
                val u = urlF.text.toString().trim()
                if (u.isEmpty()) { toast("The file address is needed."); return@setPositiveButton }
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
