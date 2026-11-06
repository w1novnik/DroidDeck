package com.droiddeck.launcher.stores.xbox

import com.droiddeck.launcher.stores.CatalogItem
import com.droiddeck.launcher.stores.Store
import org.json.JSONObject

internal object XboxPackagePolicy {
    const val UBISOFT = "ubisoft"
    const val EA = "ea"
    const val OTHER = "external"

    fun packages(product: JSONObject): List<JSONObject> = XboxCatalog.objects(product.optJSONArray("DisplaySkuAvailabilities")).flatMap {
        XboxCatalog.objects(it.optJSONObject("Sku")?.optJSONObject("Properties")?.optJSONArray("Packages"))
    }

    fun desktop(pkg: JSONObject): Boolean = XboxCatalog.objects(pkg.optJSONArray("PlatformDependencies"))
        .any { it.optString("PlatformName") == "Windows.Desktop" }

    fun placeholder(pkg: JSONObject): Boolean {
        val applications = XboxCatalog.objects(pkg.optJSONArray("Applications"))
        return applications.isNotEmpty() && applications.all { it.optString("ApplicationId").equals("GDKStubGame", true) }
    }

    fun launcher(packages: List<JSONObject>): String? {
        val stubs = packages.filter(::placeholder)
        if (stubs.isEmpty()) return null
        return when {
            stubs.all { it.optString("PackageFamilyName").startsWith("Ubisoft.", true) } -> UBISOFT
            stubs.all { it.optString("PackageFamilyName").startsWith("ElectronicArtsMobile.", true) } -> EA
            else -> OTHER
        }
    }

    fun externalProduct(product: JSONObject): String? = product.optJSONObject("Properties")?.optString("PackageFamilyName")
        ?.takeIf { it.startsWith("ElectronicArtsMobile.", true) }?.let { EA }

    data class Selection(val choices: List<JSONObject>, val externalLauncher: String?)

    fun selection(choices: List<JSONObject>, products: List<JSONObject>): Selection {
        val byId = products.associateBy { it.optString("ProductId").uppercase() }
        val stubs = ArrayList<JSONObject>()
        val playable = choices.filter { choice ->
            val product = byId[choice.getString("productId").uppercase()] ?: error("Xbox package metadata is unavailable. Try again.")
            val matching = packages(product).filter {
                desktop(it) && it.optString("PackageFormat").equals("MSIXVC", true) &&
                    it.optString("ContentId").equals(choice.getString("contentId"), true)
            }
            check(matching.isNotEmpty()) { "Xbox package metadata changed. Try again." }
            val blocked = matching.all(::placeholder)
            if (blocked) stubs.addAll(matching)
            !blocked
        }
        return Selection(playable, launcher(stubs))
    }
}

val CatalogItem.xboxExternalLauncher: String?
    get() = extra["externalLauncher"]?.takeIf { store == Store.XBOX && it in setOf(XboxPackagePolicy.UBISOFT, XboxPackagePolicy.EA, XboxPackagePolicy.OTHER) }
