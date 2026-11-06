package com.droiddeck.launcher.stores

import android.content.Context
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import com.droiddeck.launcher.frontend.AddedGames
import org.json.JSONObject
import java.io.File

object PublisherLaunchers {
    const val PROGRAM = "/usr/local/bin/droiddeck-publisher"
    const val REQUEST = ".droiddeck-publisher-request.json"
    const val RESULT = ".droiddeck-publisher-result.json"
    const val EXE = ".droiddeck-publisher-launch.bat"
    private val providers = setOf("ubisoft", "ea")

    data class Launch(val provider: String, val request: String)
    var pendingLaunch by mutableStateOf<Launch?>(null)
        private set

    fun takeLaunch(): Launch? = pendingLaunch.also { pendingLaunch = null }

    fun provider(item: CatalogItem?): String? = item?.extra?.let { it["publisherLauncher"] ?: it["externalLauncher"] }?.takeIf { it in providers }
    fun managed(sidecar: StoreGameSidecar?): Boolean = sidecar?.extra?.get("publisherManaged") == "1" && sidecar.extra["publisherLauncher"] in providers
    fun label(provider: String) = if (provider == "ea") "EA app" else "Ubisoft Connect"
    fun epicProvider(value: String?): String? = when (value?.lowercase()) {
        "ubisoftconnect" -> "ubisoft"
        "origin", "the ea app" -> "ea"
        else -> null
    }

    fun request(context: Context, item: CatalogItem, root: File, provider: String, action: String = "install") {
        require(provider in providers)
        require(action in setOf("install", "manage", "uninstall"))
        val app = context.applicationContext
        Thread({
            runCatching {
                val folder = StoreInstallRoot.folderFor(app, item.store, item.id, item.title, root)
                val guest = AddedGames.guestPath(app, folder) ?: error("The game folder is not available in the runtime.")
                folder.mkdirs()
                val before = StoreGameSidecar.read(folder)?.takeIf(::managed)
                val extra = before?.extra.orEmpty() + item.extra + mapOf("publisherLauncher" to provider, "publisherManaged" to "1")
                val template = StoreGameSidecar(item.store, item.id, item.title, EXE, cover = item.tallImageUrl, hero = item.imageUrl,
                    cloud = false, extra = extra)
                val download = File(root, "PublisherGames/${label(provider)}").apply { mkdirs() }
                val body = JSONObject().put("schema", 1).put("provider", provider).put("title", item.title).put("action", action)
                    .put("subscription", item.subscription || item.includedWith.isNotBlank())
                    .put("store", item.store.id).put("id", item.id).put("offerId", extra["publisherGameId"].orEmpty())
                    .put("downloadRoot", AddedGames.guestPath(app, download) ?: error("The download folder is not available in the runtime."))
                    .put("sidecar", template.toJson())
                val file = File(folder, REQUEST)
                val stage = File(folder, "$REQUEST.tmp")
                stage.writeText(body.toString())
                java.nio.file.Files.move(stage.toPath(), file.toPath(), java.nio.file.StandardCopyOption.REPLACE_EXISTING)
                StoreInstalls.begin(folder, item.store, item.id, item.title, item.tallImageUrl, item.imageUrl)
                StoresState.post { pendingLaunch = Launch(provider, guest) }
            }.onFailure { error -> StoresState.post { StoresState.problems[item.store] = error.message ?: "Publisher launcher could not start." } }
        }, "publisher-install").start()
    }

    private fun validate(wanted: JSONObject, data: JSONObject): String {
        val provider = wanted.getString("provider")
        require(provider in providers && wanted.getInt("schema") == 1 && data.getInt("schema") == 1 && data.getString("provider") == provider)
        require(data.getString("store") == wanted.getString("store") && data.getString("id") == wanted.getString("id"))
        return provider
    }

    fun epicRequest(context: Context, id: String): Boolean = StoreInstallRoot.gameFolders(context).any { folder ->
        runCatching {
            val file = File(folder, REQUEST)
            if (!file.isFile || file.length() > 1_048_576) return@runCatching false
            val request = JSONObject(file.readText())
            request.getInt("schema") == 1 && request.getString("store") == Store.EPIC.id && request.getString("id") == id &&
                request.getString("provider") in providers
        }.getOrDefault(false)
    }

    internal fun completed(wanted: JSONObject, data: JSONObject, hostPath: (String) -> File?): StoreGameSidecar {
        val provider = validate(wanted, data)
        val offer = data.getString("offerId")
        require(offer.matches(if (provider == "ubisoft") Regex("[0-9]{1,12}") else Regex("[A-Za-z0-9._:-]{1,160}")))
        val path = data.getString("path")
        val installed = hostPath(path) ?: error("Publisher game folder is unavailable.")
        require(installed.isDirectory && path.startsWith('/') && path.split('/').none { it == ".." })
        val exe = data.getString("executable").replace('\\', '/')
        require(exe.isNotEmpty() && !exe.startsWith('/') && ':' !in exe && exe.split('/').none { it == ".." || it.isEmpty() })
        val executable = File(installed, exe)
        require(executable.isFile && executable.canonicalPath.startsWith(installed.canonicalPath + File.separator))
        val sidecar = StoreGameSidecar.parse(wanted.getJSONObject("sidecar").toString()) ?: error("Invalid publisher install record.")
        require(sidecar.store.id == wanted.getString("store") && sidecar.id == wanted.getString("id") && managed(sidecar) && sidecar.extra["publisherLauncher"] == provider)
        return sidecar.copy(extra = sidecar.extra + mapOf("publisherGameId" to offer, "publisherGamePath" to path), installedAt = System.currentTimeMillis())
    }

    @Synchronized fun importCompleted(context: Context) {
        val app = context.applicationContext
        for (folder in StoreInstallRoot.gameFolders(app)) {
            val result = File(folder, RESULT)
            if (!result.isFile) continue
            runCatching {
                require(result.length() <= 65_536)
                val data = JSONObject(result.readText())
                val request = File(folder, REQUEST)
                require(request.length() in 1..1_048_576)
                val wanted = JSONObject(request.readText())
                validate(wanted, data)
                if (data.optBoolean("removed") && wanted.optString("action") == "uninstall") {
                    StoreGameSidecar.read(folder)?.takeIf(::managed)?.let { StoreInstalls.uninstall(app, InstalledStoreGame(it, folder)) }
                    return@runCatching
                }
                val sidecar = completed(wanted, data) { AddedGames.hostPath(app, it) }
                if (StoreGameSidecar.read(folder)?.let { managed(it) && it.extra == sidecar.extra && it.isInstalled } == true) return@runCatching
                File(folder, EXE).writeText("@echo off\r\necho Start this game from DroidDeck.\r\nexit /b 1\r\n")
                StoreInstalls.complete(app, folder, sidecar)
            }.onFailure { android.util.Log.w("PublisherLaunchers", "Install is not ready: ${it.javaClass.simpleName}") }
        }
    }
}
