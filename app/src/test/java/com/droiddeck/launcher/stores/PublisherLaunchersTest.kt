package com.droiddeck.launcher.stores

import java.io.File
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

@org.junit.runner.RunWith(org.robolectric.RobolectricTestRunner::class)
@org.robolectric.annotation.Config(sdk = [33], application = android.app.Application::class)
class PublisherLaunchersTest {
    @get:Rule val temporary = TemporaryFolder()
    private fun template(provider: String = "ubisoft") = StoreGameSidecar(Store.XBOX, "9MVQXW1RJVCN", "Far Cry 5", PublisherLaunchers.EXE,
        cloud = false, extra = mapOf("publisherLauncher" to provider, "publisherManaged" to "1", "access" to "gamepass"))
    private fun request(provider: String = "ubisoft") = JSONObject().put("schema", 1).put("provider", provider).put("store", "xbox")
        .put("id", "9MVQXW1RJVCN").put("sidecar", template(provider).toJson())
    private fun result(provider: String = "ubisoft") = JSONObject().put("schema", 1).put("provider", provider).put("store", "xbox")
        .put("id", "9MVQXW1RJVCN").put("offerId", if (provider == "ubisoft") "1803" else "Origin.OFR.50.0004262")
        .put("path", "/games/native").put("executable", "Game.exe")

    @Test fun installedRecordsReferToThePublisherClientAndPreserveSubscriptionAccess() {
        val native = temporary.newFolder()
        File(native, "Game.exe").writeBytes(byteArrayOf(77, 90))
        for (provider in listOf("ubisoft", "ea")) {
            val sidecar = PublisherLaunchers.completed(request(provider), result(provider)) { native }
            assertTrue(PublisherLaunchers.managed(sidecar))
            assertEquals(PublisherLaunchers.EXE, sidecar.exe)
            assertEquals("gamepass", sidecar.extra["access"])
            assertEquals("/games/native", sidecar.extra["publisherGamePath"])
            assertFalse(sidecar.cloud)
        }
    }

    @Test fun noInstalledRecordCanBeProducedWithoutGameFiles() {
        val empty = temporary.newFolder()
        assertThrows(IllegalArgumentException::class.java) { PublisherLaunchers.completed(request(), result()) { empty } }
        assertThrows(IllegalStateException::class.java) { PublisherLaunchers.completed(request(), result()) { null } }
    }

    @Test fun aResultCannotCompleteAnotherGameOrStore() {
        val folder = temporary.newFolder()
        File(folder, "Game.exe").writeBytes(byteArrayOf(77, 90))
        for ((field, value) in listOf("id" to "9AAAAAAAAAAA", "store" to "epic", "provider" to "ea")) {
            assertThrows(IllegalArgumentException::class.java) { PublisherLaunchers.completed(request(), result().put(field, value)) { folder } }
        }
    }

    @Test fun nativeExecutablePathsCannotEscapeTheDownloadedGame() {
        val folder = temporary.newFolder()
        for (exe in listOf("../Other.exe", "C:/Other.exe", "/Other.exe", "bin/../../Other.exe")) {
            assertThrows(IllegalArgumentException::class.java) { PublisherLaunchers.completed(request(), result().put("executable", exe)) { folder } }
        }
    }

    @Test fun providerIdsMustMatchTheNativeClientProtocol() {
        for (provider in listOf("ubisoft", "ea")) {
            assertThrows(IllegalArgumentException::class.java) {
                PublisherLaunchers.completed(request(provider), result(provider).put("offerId", "1 & command")) { temporary.root }
            }
        }
    }

    @Test fun havingAClientNameAloneDoesNotMakeAPackageAnInstalledPublisherGame() {
        assertFalse(PublisherLaunchers.managed(template().copy(extra = mapOf("publisherLauncher" to "ubisoft"))))
        assertFalse(PublisherLaunchers.managed(template().copy(extra = mapOf("publisherManaged" to "1", "publisherLauncher" to "unknown"))))
        assertFalse(PublisherLaunchers.managed(null))
    }

    @Test fun epicRoutesOnlyExplicitPartnerStoreIdentifiers() {
        assertEquals("ubisoft", PublisherLaunchers.epicProvider("UbisoftConnect"))
        assertEquals("ea", PublisherLaunchers.epicProvider("Origin"))
        assertEquals("ea", PublisherLaunchers.epicProvider("The EA App"))
        assertNull(PublisherLaunchers.epicProvider("Electronic Arts"))
        assertNull(PublisherLaunchers.epicProvider(null))
    }
}
