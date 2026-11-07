package com.droiddeck.launcher.stores.xbox

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

@org.junit.runner.RunWith(org.robolectric.RobolectricTestRunner::class)
@org.robolectric.annotation.Config(sdk = [33], application = android.app.Application::class)
class XboxPackagePolicyTest {
    @get:Rule val temporary = TemporaryFolder()

    private fun products(): List<JSONObject> {
        val text = javaClass.getResourceAsStream("/xbox/ubisoft-placeholder-packages.json")!!.bufferedReader().use { it.readText() }
        return XboxCatalog.objects(JSONArray(text))
    }

    private fun bundle(): JSONObject = JSONObject(javaClass.getResourceAsStream("/xbox/ubisoft-pc-bundle.json")!!.bufferedReader().use { it.readText() })

    @Test fun theLostCrownBundleKeepsTheLicensedCardAndUsesItsPrimaryPcProduct() {
        val root = bundle()
        val child = products().first()
        assertNull(XboxCatalog.product(root, mapOf(root.getString("ProductId") to "owned")))
        val cards = XboxCatalog.libraryItems(listOf(root, child), mapOf(root.getString("ProductId") to "owned"))
        val card = cards.single()
        assertEquals(root.getString("ProductId"), card.id)
        assertEquals("ubisoft", card.xboxExternalLauncher)
        assertTrue(card.owned)
        assertFalse(card.xboxPc)
        assertEquals(0L, card.sizeBytes)
    }

    @Test fun aBundleKeepsSubscriptionAndCloudRightsOnItsOwnId() {
        val root = bundle()
        val id = root.getString("ProductId")
        val card = XboxCatalog.libraryItems(listOf(root, products().first()), mapOf(id to "gamepass"), XboxCloudCatalog(setOf(id), emptySet())).single()
        assertEquals(id, card.id)
        assertTrue(card.subscription)
        assertTrue(card.xboxCloud)
        assertFalse(card.owned)
    }

    @Test fun unlicensedPrimaryProductsAreNotAddedAsSeparateLibraryGames() {
        val root = bundle()
        val child = products().first()
        assertTrue(XboxCatalog.libraryItems(listOf(root, child), emptyMap()).isEmpty())
        assertEquals(listOf(child.getString("ProductId")), XboxCatalog.libraryItems(listOf(root, child), mapOf(child.getString("ProductId") to "owned")).map { it.id })
    }

    @Test fun optionalBundleContentsDoNotMakeTheBaseProductInstallable() {
        val root = bundle()
        root.getJSONArray("DisplaySkuAvailabilities").getJSONObject(0).getJSONObject("Sku").getJSONObject("Properties")
            .getJSONArray("BundledSkus").getJSONObject(0).put("IsPrimary", false)
        assertTrue(XboxPackagePolicy.primaryProducts(root).isEmpty())
        assertTrue(XboxCatalog.libraryItems(listOf(root, products().first()), mapOf(root.getString("ProductId") to "owned")).isEmpty())
    }

    @Test fun missingOrCyclicBundleMetadataDoesNotInventAPcDownload() {
        val root = bundle()
        val id = root.getString("ProductId")
        assertTrue(XboxCatalog.libraryItems(listOf(root), mapOf(id to "owned")).isEmpty())
        root.getJSONArray("DisplaySkuAvailabilities").getJSONObject(0).getJSONObject("Sku").getJSONObject("Properties")
            .getJSONArray("BundledSkus").getJSONObject(0).put("BigId", id)
        assertTrue(XboxCatalog.libraryItems(listOf(root), mapOf(id to "owned")).isEmpty())
    }

    @Test fun aRealPrimaryPcPackageKeepsItsDownloadSizeAndBundleIdentity() {
        val root = bundle()
        val child = products().first()
        application(child, "Game")
        val card = XboxCatalog.libraryItems(listOf(root, child), mapOf(root.getString("ProductId") to "owned")).single()
        assertEquals(root.getString("ProductId"), card.id)
        assertTrue(card.xboxPc)
        assertNull(card.xboxExternalLauncher)
        assertEquals(10_612_736L, card.sizeBytes)
    }

    private fun choice(product: JSONObject): JSONObject = JSONObject().put("productId", product.getString("ProductId"))
        .put("contentId", XboxPackagePolicy.packages(product).first().getString("ContentId"))

    private fun application(product: JSONObject, id: String) {
        XboxPackagePolicy.packages(product).first().getJSONArray("Applications").getJSONObject(0).put("ApplicationId", id)
    }

    @Test fun actualUbisoftPackagesAreVisibleWithoutOfferingTheStubAsTheGame() {
        val products = products()
        assertEquals(4, products.size)
        for (product in products) {
            val id = product.getString("ProductId")
            val card = XboxCatalog.product(product, mapOf(id to "owned"))!!
            assertEquals("ubisoft", card.xboxExternalLauncher)
            assertFalse(card.xboxPc)
            assertTrue(card.owned)
            assertEquals(0L, card.sizeBytes)
        }
    }

    @Test fun placeholderClassificationPreservesCloudAccessAndSubscriptionStatus() {
        val product = products().first()
        val id = product.getString("ProductId")
        val card = XboxCatalog.product(product, mapOf(id to "gamepass"), XboxCloudCatalog(setOf(id), emptySet()))!!
        assertTrue(card.xboxCloud)
        assertTrue(card.subscription)
        assertFalse(card.owned)
        assertFalse(card.xboxPc)
        assertEquals("ubisoft", card.xboxExternalLauncher)
    }

    @Test fun anExternalPcEntryKeepsTheSeparateCloudEditionWhenCardsAreMerged() {
        val product = products().first()
        val id = product.getString("ProductId")
        val external = XboxCatalog.product(product, mapOf(id to "gamepass"))!!
        val cloud = external.copy(id = "9AAAAAAAAAAA", extra = external.extra - "externalLauncher" + mapOf("pc" to "0", "cloud" to "1"))
        val merged = mergeEditions(listOf(external, cloud)).single()
        assertEquals(id, merged.id)
        assertFalse(merged.xboxPc)
        assertEquals("ubisoft", merged.xboxExternalLauncher)
        assertTrue(merged.xboxCloud)
        assertEquals(cloud.id, merged.xboxCloudId)
    }

    @Test fun smallUbisoftGamesAndOtherLaunchersAreNotRejectedBySizeOrPublisher() {
        for (appId in listOf("Game", "GDKStubGameHelper", "MinecraftLauncher")) {
            val product = products().first()
            application(product, appId)
            val card = XboxCatalog.product(product, mapOf(product.getString("ProductId") to "owned"))!!
            assertTrue(card.xboxPc)
            assertNull(card.xboxExternalLauncher)
            assertEquals(10_612_736L, card.sizeBytes)
        }
    }

    @Test fun placeholderIdIsCaseInsensitiveAndAnUnknownPublisherGetsGenericGuidance() {
        val product = products().first()
        application(product, "gdkstubgame")
        XboxPackagePolicy.packages(product).first().put("PackageFamilyName", "AnotherPublisher.Game_123")
        val card = XboxCatalog.product(product, emptyMap())!!
        assertEquals("external", card.xboxExternalLauncher)
        assertFalse(card.xboxPc)
        assertNull(card.copy(store = com.droiddeck.launcher.stores.Store.GOG).xboxExternalLauncher)
    }

    @Test fun consolePlaceholdersDoNotBecomePcInstallOffers() {
        val product = products().first()
        XboxPackagePolicy.packages(product).first().getJSONArray("PlatformDependencies").getJSONObject(0).put("PlatformName", "Xbox.One")
        assertNull(XboxCatalog.product(product, emptyMap()))
    }

    @Test fun eaXboxBridgeProductsUseEaAppWithoutClaimingANativeMsixvcDownload() {
        val product = JSONObject().put("ProductId", "9NVQ4ZLJGMDV").put("ProductType", "Game")
            .put("LocalizedProperties", JSONArray().put(JSONObject().put("ProductTitle", "It Takes Two")))
            .put("Properties", JSONObject().put("PackageFamilyName", "ElectronicArtsMobile.Rosebud-Nuts_q5ha1ztykcgvj"))
        val card = XboxCatalog.product(product, mapOf("9NVQ4ZLJGMDV" to "owned"))!!
        assertEquals("ea", card.xboxExternalLauncher)
        assertTrue(card.owned)
        assertFalse(card.xboxPc)
        assertEquals(0L, card.sizeBytes)
        val cached = XboxLibraryCache.decode(XboxLibraryCache.encode("test@example.test", "US", listOf(card)), "test@example.test", "US")!!
        assertEquals("ea", cached.games.single().xboxExternalLauncher)
    }

    @Test fun aRealEaMsixvcGameStillUsesTheNormalInstaller() {
        val product = products().first()
        application(product, "Game")
        product.put("Properties", JSONObject().put("PackageFamilyName", "ElectronicArtsMobile.Game_q5ha1ztykcgvj"))
        val card = XboxCatalog.product(product, emptyMap())!!
        assertTrue(card.xboxPc)
        assertNull(card.xboxExternalLauncher)
    }

    @Test fun placeholderDetectionDoesNotDependOnDownloadSize() {
        val product = products().first()
        XboxPackagePolicy.packages(product).first().put("MaxDownloadSizeInBytes", 50_000_000_000L)
        val card = XboxCatalog.product(product, emptyMap())!!
        assertFalse(card.xboxPc)
        assertEquals("ubisoft", card.xboxExternalLauncher)
        assertEquals(0L, card.sizeBytes)
    }

    @Test fun anotherApplicationInTheSamePackageRemainsInstallable() {
        val product = products().first()
        XboxPackagePolicy.packages(product).first().getJSONArray("Applications").put(JSONObject().put("ApplicationId", "Game"))
        val card = XboxCatalog.product(product, emptyMap())!!
        assertTrue(card.xboxPc)
        assertNull(card.xboxExternalLauncher)
    }

    @Test fun aRealPackageAlongsideAStubKeepsItsOwnDownloadSize() {
        val product = products().first()
        val packages = product.getJSONArray("DisplaySkuAvailabilities").getJSONObject(0).getJSONObject("Sku")
            .getJSONObject("Properties").getJSONArray("Packages")
        val real = JSONObject(packages.getJSONObject(0).toString()).put("MaxDownloadSizeInBytes", 1234L)
            .put("Applications", JSONArray().put(JSONObject().put("ApplicationId", "Game")))
        packages.put(real)
        val card = XboxCatalog.product(product, emptyMap())!!
        assertTrue(card.xboxPc)
        assertNull(card.xboxExternalLauncher)
        assertEquals(1234L, card.sizeBytes)
    }

    @Test fun anUnsupportedPcPackageDoesNotInflateTheSizeOfAnInstallablePackage() {
        val product = products().first()
        application(product, "Game")
        val packages = product.getJSONArray("DisplaySkuAvailabilities").getJSONObject(0).getJSONObject("Sku")
            .getJSONObject("Properties").getJSONArray("Packages")
        packages.put(JSONObject(packages.getJSONObject(0).toString()).put("PackageFormat", "APPX").put("MaxDownloadSizeInBytes", 999_999_999L))
        assertEquals(10_612_736L, XboxCatalog.product(product, emptyMap())!!.sizeBytes)
    }

    @Test fun livePackageSelectionRejectsAllFourRealWorldStubs() {
        val products = products()
        val selected = XboxPackagePolicy.selection(products.map(::choice), products)
        assertTrue(selected.choices.isEmpty())
        assertEquals("ubisoft", selected.externalLauncher)
    }

    @Test fun aBundleWithBothStubAndPlayableChoicesKeepsThePlayableOne() {
        val products = products()
        application(products[1], "Game")
        val choices = products.map(::choice)
        val selected = XboxPackagePolicy.selection(choices, products)
        assertEquals(listOf(choices[1]), selected.choices)
    }

    @Test fun anUnrelatedStubDoesNotBlockAnotherContentIdInTheSameProduct() {
        val product = products().first()
        val packages = product.getJSONArray("DisplaySkuAvailabilities").getJSONObject(0).getJSONObject("Sku")
            .getJSONObject("Properties").getJSONArray("Packages")
        packages.put(JSONObject(packages.getJSONObject(0).toString()).put("ContentId", "11111111-1111-1111-1111-111111111111"))
        application(product, "Game")
        val choice = choice(product)
        val selected = XboxPackagePolicy.selection(listOf(choice), listOf(product))
        assertEquals(listOf(choice), selected.choices)
        assertNull(selected.externalLauncher)
    }

    @Test fun unavailableOrChangedSelectionCannotSilentlySkipValidation() {
        val product = products().first()
        val choice = choice(product)
        assertThrows(IllegalStateException::class.java) { XboxPackagePolicy.selection(listOf(choice), emptyList()) }
        choice.put("contentId", "11111111-1111-1111-1111-111111111111")
        assertThrows(IllegalStateException::class.java) { XboxPackagePolicy.selection(listOf(choice), listOf(product)) }
    }

    @Test fun placeholderFlagsAndCloudAccessSurviveTheLibraryCache() {
        val product = products().first()
        val id = product.getString("ProductId")
        val card = XboxCatalog.product(product, mapOf(id to "gamepass"), XboxCloudCatalog(setOf(id), emptySet()))!!
        val text = XboxLibraryCache.encode("test@example.test", "US", listOf(card))
        val restored = XboxLibraryCache.decode(text, "test@example.test", "US")!!.games.single()
        assertEquals("ubisoft", restored.xboxExternalLauncher)
        assertTrue(restored.xboxCloud)
        assertFalse(restored.xboxPc)
        assertEquals(0L, restored.sizeBytes)
    }

    private fun manifest(vararg executables: String): java.io.File {
        val folder = temporary.newFolder()
        java.io.File(folder, "MicrosoftGame.config").writeText("<Game><ExecutableList>${executables.joinToString("")}</ExecutableList></Game>")
        return folder
    }

    @Test fun downloadedPlaceholderIsRejectedEvenIfItsExeHasAnotherName() {
        val folder = manifest("""<Executable Id="GDKStubGame" Name="Launcher.exe" TargetDeviceFamily="PC"/>""")
        java.io.File(folder, "Launcher.exe").writeText("stub")
        assertTrue(XboxManifest.read(folder).placeholder)
        assertThrows(IllegalStateException::class.java) { XboxManifest.executable(folder) }
    }

    @Test fun legacyPlaceholderIsRecognizedByItsExecutableWhenIdIsMissing() {
        val folder = manifest("""<Executable Name="gdkstubgame.EXE"/>""")
        java.io.File(folder, "gdkstubgame.EXE").writeText("stub")
        assertTrue(XboxManifest.read(folder).placeholder)
        assertThrows(IllegalStateException::class.java) { XboxManifest.executable(folder) }
    }

    @Test fun retailExecutableStillLaunchesWhenADevOrConsoleStubIsPresent() {
        for (attributes in listOf("IsDevOnly=\"true\"", "TargetDeviceFamily=\"XboxOne\"", "")) {
            val folder = manifest("""<Executable Id="GDKStubGame" Name="Stub.exe" $attributes/>""", """<Executable Name="Game.exe"/>""")
            java.io.File(folder, "Game.exe").writeText("game")
            assertFalse(XboxManifest.read(folder).placeholder)
            assertEquals("Game.exe", XboxManifest.executable(folder))
        }
    }
}
