package net.aniello.mediamanager

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileInputStream
import java.io.RandomAccessFile
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

/** One library item, as served by GET /media-admin/list. */
data class MediaItem(val key: String, val title: String, val size: Long, val poster: Boolean) {
    val posterKey: String get() = key.replace(Regex("\\.(mp4|webm)$", RegexOption.IGNORE_CASE), "-poster.jpg")
}

/**
 * Thin client for the dashboard-authenticated media-admin API on
 * ljm3.me (Worker handlers added 2026-11-12). Every call carries the
 * saved dashboard sign-in; nothing else about the server is assumed.
 */
object Api {
    const val BASE = "https://ljm3.me"
    const val PART_BYTES = 50L * 1024 * 1024

    class ApiException(val code: Int, message: String) : Exception(message)

    private fun connect(context: Context, url: String, method: String): HttpURLConnection {
        val conn = URL(url).openConnection() as HttpURLConnection
        conn.requestMethod = method
        conn.useCaches = false
        conn.setRequestProperty("Cache-Control", "no-cache")
        conn.setRequestProperty("User-Agent", "MediaManager/" + BuildConfig.VERSION_NAME)
        val auth = Credentials.authHeader(context)
        if (auth != null) conn.setRequestProperty("Authorization", auth)
        conn.connectTimeout = 20_000
        conn.readTimeout = 60_000
        return conn
    }

    private fun readBody(conn: HttpURLConnection): String {
        val stream = if (conn.responseCode in 200..299) conn.inputStream else conn.errorStream
        return stream?.bufferedReader()?.readText() ?: ""
    }

    private fun enc(s: String): String = URLEncoder.encode(s, "UTF-8")

    fun list(context: Context, type: String): List<MediaItem> {
        val conn = connect(context, "$BASE/media-admin/list?type=$type", "GET")
        val body = readBody(conn)
        if (conn.responseCode != 200) throw ApiException(conn.responseCode, "List failed (HTTP ${conn.responseCode}).")
        val arr = JSONArray(body)
        val out = ArrayList<MediaItem>()
        for (i in 0 until arr.length()) {
            val o = arr.getJSONObject(i)
            out.add(MediaItem(o.getString("key"), o.optString("title", ""), o.optLong("size", 0), o.optBoolean("poster", false)))
        }
        return out
    }

    /** Raw bytes of a stored file (thumbnails / posters), via the dashboard-gated file route. */
    fun fetchFileBytes(context: Context, key: String): ByteArray {
        val conn = connect(context, "$BASE/media-admin/file?k=${enc(key)}", "GET")
        if (conn.responseCode != 200) throw ApiException(conn.responseCode, "File fetch failed (HTTP ${conn.responseCode}).")
        return conn.inputStream.use { it.readBytes() }
    }

    private fun postJson(context: Context, path: String, payload: JSONObject): JSONObject {
        val conn = connect(context, BASE + path, "POST")
        conn.doOutput = true
        conn.setRequestProperty("Content-Type", "application/json")
        val bytes = payload.toString().toByteArray(Charsets.UTF_8)
        conn.setFixedLengthStreamingMode(bytes.size)
        conn.outputStream.use { it.write(bytes) }
        val body = readBody(conn)
        if (conn.responseCode != 200) {
            val msg = try { JSONObject(body).optString("error") } catch (e: Exception) { "" }
            throw ApiException(conn.responseCode, if (msg.isNotEmpty()) msg else "Request failed (HTTP ${conn.responseCode}).")
        }
        return if (body.isBlank()) JSONObject() else JSONObject(body)
    }

    fun rename(context: Context, from: String, to: String) {
        postJson(context, "/media-admin/rename", JSONObject().put("from", from).put("to", to))
    }

    fun delete(context: Context, key: String) {
        postJson(context, "/media-admin/delete", JSONObject().put("key", key))
    }

    // No bulk reorder call: the server's /media-admin/reorder copies every
    // changed item twice in one request (fatal for multi-GB videos) and is
    // refused for videos. Since v1.1 the app reorders step-wise through
    // rename() — see MainActivity.saveOrder.

    fun importUrl(context: Context, url: String, type: String, title: String, year: String): String {
        val r = postJson(context, "/media-admin/import-url",
            JSONObject().put("url", url).put("type", type).put("title", title).put("year", year))
        return r.getString("key")
    }

    /** One Dropbox entry, as served by GET /media-admin/dropbox/list. */
    data class DbxEntry(val tag: String, val name: String, val path: String, val size: Long)

    /**
     * Browse the connected Dropbox (Stage 2): the server holds the
     * Dropbox sign-in and lists with it; the app never sees a token.
     */
    fun dropboxList(context: Context, path: String): List<DbxEntry> {
        val conn = connect(context, "$BASE/media-admin/dropbox/list?path=${enc(path)}", "GET")
        conn.readTimeout = 120_000
        val body = readBody(conn)
        if (conn.responseCode != 200) {
            val msg = try { JSONObject(body).optString("error") } catch (e: Exception) { "" }
            throw ApiException(conn.responseCode, if (msg.isNotEmpty()) msg else "Dropbox list failed (HTTP ${conn.responseCode}).")
        }
        val arr = JSONArray(body)
        val out = ArrayList<DbxEntry>()
        for (i in 0 until arr.length()) {
            val o = arr.getJSONObject(i)
            out.add(DbxEntry(o.getString("tag"), o.getString("name"), o.optString("path", ""), o.optLong("size", 0)))
        }
        return out
    }

    /** Server-side copy of one Dropbox file into the library; returns the stored key. */
    fun dropboxImport(context: Context, path: String, type: String, title: String, year: String): String {
        val r = postJson(context, "/media-admin/dropbox/import",
            JSONObject().put("path", path).put("type", type).put("title", title).put("year", year))
        return r.getString("key")
    }

    /** Single-shot PUT (photos). Reports byte progress; returns the stored size. */
    fun putFile(context: Context, key: String, file: File, onProgress: (Long, Long) -> Unit): Long {
        val conn = connect(context, "$BASE/media-admin/put?key=${enc(key)}", "PUT")
        conn.doOutput = true
        conn.readTimeout = 600_000
        conn.setFixedLengthStreamingMode(file.length())
        val total = file.length()
        var sent = 0L
        FileInputStream(file).use { input ->
            conn.outputStream.use { output ->
                val buf = ByteArray(256 * 1024)
                while (true) {
                    val n = input.read(buf)
                    if (n < 0) break
                    output.write(buf, 0, n)
                    sent += n
                    onProgress(sent, total)
                }
            }
        }
        val body = readBody(conn)
        if (conn.responseCode != 200) throw ApiException(conn.responseCode, "Upload failed (HTTP ${conn.responseCode}).")
        return JSONObject(body).optLong("size", total)
    }

    /** Single-shot PUT of in-memory bytes (video posters). Returns the stored size. */
    fun putBytes(context: Context, key: String, bytes: ByteArray): Long {
        val conn = connect(context, "$BASE/media-admin/put?key=${enc(key)}", "PUT")
        conn.doOutput = true
        conn.readTimeout = 120_000
        conn.setFixedLengthStreamingMode(bytes.size)
        conn.outputStream.use { it.write(bytes) }
        val body = readBody(conn)
        if (conn.responseCode != 200) throw ApiException(conn.responseCode, "Upload failed (HTTP ${conn.responseCode}).")
        return JSONObject(body).optLong("size", bytes.size.toLong())
    }

    /**
     * Multipart upload (videos) in 50 MiB parts through mp-init /
     * mp-part / mp-complete. On any failure the upload is aborted
     * server-side so no partial object or dangling session remains.
     */
    fun multipartUpload(context: Context, key: String, file: File, onProgress: (Long, Long) -> Unit): Long {
        val init = postJson(context, "/media-admin/mp-init", JSONObject().put("key", key))
        val uploadId = init.getString("uploadId")
        val total = file.length()
        val parts = JSONArray()
        try {
            RandomAccessFile(file, "r").use { raf ->
                var offset = 0L
                var partNo = 1
                while (offset < total || (total == 0L && partNo == 1)) {
                    val len = if (total == 0L) 0 else minOf(PART_BYTES, total - offset).toInt()
                    val buf = ByteArray(len)
                    if (len > 0) { raf.seek(offset); raf.readFully(buf) }
                    val partUrl = "$BASE/media-admin/mp-part?key=${enc(key)}&uploadId=${enc(uploadId)}&part=$partNo"
                    val conn = connect(context, partUrl, "POST")
                    conn.doOutput = true
                    conn.readTimeout = 900_000
                    conn.setFixedLengthStreamingMode(len)
                    conn.outputStream.use { it.write(buf) }
                    val body = readBody(conn)
                    if (conn.responseCode != 200) throw ApiException(conn.responseCode, "Part $partNo failed (HTTP ${conn.responseCode}).")
                    val pj = JSONObject(body)
                    parts.put(JSONObject().put("partNumber", pj.getInt("partNumber")).put("etag", pj.getString("etag")))
                    offset += len
                    onProgress(offset, total)
                    partNo++
                    if (total == 0L) break
                }
            }
            val done = postJson(context, "/media-admin/mp-complete",
                JSONObject().put("key", key).put("uploadId", uploadId).put("parts", parts))
            return done.optLong("size", total)
        } catch (e: Exception) {
            try { postJson(context, "/media-admin/mp-abort", JSONObject().put("key", key).put("uploadId", uploadId)) } catch (ignored: Exception) { }
            throw e
        }
    }

    /** Downloads a file to the app cache (the WebDAV / network-share relay path). */
    fun downloadToCache(context: Context, url: String, basicAuth: String?, dest: File, onProgress: (Long, Long) -> Unit) {
        val conn = URL(url).openConnection() as HttpURLConnection
        conn.useCaches = false
        conn.setRequestProperty("User-Agent", "MediaManager/" + BuildConfig.VERSION_NAME)
        if (!basicAuth.isNullOrEmpty()) conn.setRequestProperty("Authorization", basicAuth)
        conn.connectTimeout = 20_000
        conn.readTimeout = 300_000
        if (conn.responseCode !in 200..299) throw ApiException(conn.responseCode, "Download failed (HTTP ${conn.responseCode}).")
        val total = conn.contentLengthLong
        var got = 0L
        conn.inputStream.use { input ->
            dest.outputStream().use { output ->
                val buf = ByteArray(256 * 1024)
                while (true) {
                    val n = input.read(buf)
                    if (n < 0) break
                    output.write(buf, 0, n)
                    got += n
                    onProgress(got, if (total > 0) total else got)
                }
            }
        }
    }
}
