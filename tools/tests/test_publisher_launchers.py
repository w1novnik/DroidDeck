import importlib.machinery
import importlib.util
import io
import json
import os
import runpy
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

BIN = Path(__file__).resolve().parents[1] / 'linuxfs/overlay/usr/local/bin'
loader = importlib.machinery.SourceFileLoader('publisher_launcher', str(BIN / 'droiddeck-publisher'))
spec = importlib.util.spec_from_loader(loader.name, loader)
publisher = importlib.util.module_from_spec(spec)
loader.exec_module(publisher)


class PublisherLaunchersTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.patches = [patch.object(publisher, 'STEAM', self.root / 'Steam'),
                        patch.object(publisher, 'STATE', self.root / 'clients')]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        if os.name == 'nt':
            item = patch.dict(sys.modules, {'fcntl': types.SimpleNamespace(LOCK_EX=1, LOCK_NB=2, flock=lambda *args: None)})
            item.start()
            self.addCleanup(item.stop)

    def request(self, provider='ubisoft', **values):
        folder = self.root / 'store-game'
        folder.mkdir(exist_ok=True)
        body = dict(schema=1, provider=provider, title='Far Cry 5', store='xbox', id='9MVQXW1RJVCN',
                    downloadRoot=str(self.root / 'downloads'), **values)
        (folder / publisher.REQUEST).write_text(json.dumps(body))
        return folder

    def ubisoft_game(self, title='Far Cry 5', offer='1803', payload=True):
        pfx = publisher.prefix('ubisoft')
        folder = pfx / 'drive_c/games' / title
        folder.mkdir(parents=True, exist_ok=True)
        pfx.joinpath('system.reg').write_text(
            '[Software\\\\Wow6432Node\\\\Ubisoft\\\\Launcher\\\\Installs\\\\%s] 1\n'
            '"InstallDir"="C:\\\\games\\\\%s"\n' % (offer, title))
        if payload:
            (folder / 'FarCry5.exe').write_bytes(b'MZ' + b'game payload')
        return folder

    def test_install_opens_client_then_only_registers_the_full_game(self):
        folder = self.request()
        def downloaded(*args, **kwargs):
            self.ubisoft_game()
            return 0
        with patch.object(publisher, 'ensure_launcher', return_value=Path('/UbisoftConnect.exe')), \
                patch.object(publisher, 'run_client', side_effect=downloaded) as run:
            self.assertEqual(publisher.run('ubisoft', folder), 0)
        data = json.loads((folder / publisher.RESULT).read_text())
        self.assertEqual(data['offerId'], '1803')
        self.assertEqual(data['executable'], 'FarCry5.exe')
        self.assertEqual(data['store'], 'xbox')
        self.assertEqual(run.call_args.args[2], [])

    def test_next_play_launches_the_installed_game_through_the_same_client_prefix(self):
        folder = self.request()
        self.ubisoft_game()
        with patch.object(publisher, 'ensure_launcher', return_value=Path('/UbisoftConnect.exe')), \
                patch.object(publisher, 'run_client', return_value=0) as run:
            self.assertEqual(publisher.run('ubisoft', folder), 0)
        command = publisher.command(*run.call_args.args[:3])
        self.assertEqual(command[-1], 'uplay://launch/1803/0')
        self.assertEqual(command[2], publisher.prefix_id('ubisoft'))
        self.assertEqual(publisher.environment()['DROIDDECK_PUBLISHER_ACTIVE'], '1')

    def test_launcher_or_stub_without_game_payload_never_produces_an_installed_result(self):
        folder = self.request()
        game = self.ubisoft_game(payload=False)
        (game / 'GDKStubGame.exe').write_bytes(b'MZstub')
        with patch.object(publisher, 'ensure_launcher', return_value=Path('/UbisoftConnect.exe')), \
                patch.object(publisher, 'run_client', return_value=0):
            self.assertEqual(publisher.run('ubisoft', folder), 0)
        self.assertFalse((folder / publisher.RESULT).exists())

    def test_failed_or_cancelled_client_cannot_report_installation_complete(self):
        for code in (1, 130):
            folder = self.request()
            self.ubisoft_game()
            with patch.object(publisher, 'ensure_launcher', return_value=Path('/UbisoftConnect.exe')), \
                    patch.object(publisher, 'run_client', return_value=code):
                self.assertEqual(publisher.run('ubisoft', folder), code)
            self.assertFalse((folder / publisher.RESULT).exists())

    def test_registry_readiness_requires_a_retail_executable(self):
        game = self.ubisoft_game(payload=False)
        (game / 'Setup.exe').write_bytes(b'MZsetup')
        (game / 'CrashReport.exe').write_bytes(b'MZhelper')
        self.assertEqual(publisher.records('ubisoft', self.root / 'downloads'), [])
        (game / 'FarCry5.exe').write_text('not a PE executable')
        self.assertEqual(publisher.records('ubisoft', self.root / 'downloads'), [])

    def test_ambiguous_titles_are_not_guessed(self):
        records = [dict(offerId='1', title='Far Cry 5', path='/one'), dict(offerId='2', title='Far Cry 5', path='/two')]
        self.assertIsNone(publisher.choose(records, 'Far Cry® 5'))
        self.assertEqual(publisher.choose(records, 'Far Cry® 5', '2'), records[1])
        self.assertEqual(publisher.key('STAR WARS Jedi: Survivor™'), publisher.key('Star Wars Jedi Survivor'))

    def test_an_existing_client_is_reused_without_another_installer_download(self):
        file = publisher.prefix('ea') / 'drive_c' / publisher.PROVIDERS['ea']['clients'][0]
        file.parent.mkdir(parents=True)
        file.write_bytes(b'MZclient')
        file.with_name('EADesktop.exe').write_bytes(b'MZdesktop')
        with patch.object(publisher, 'download') as download:
            self.assertEqual(publisher.ensure_launcher('ea', self.root / 'downloads'), file)
        download.assert_not_called()

    def test_a_partial_client_executable_is_not_treated_as_an_installed_launcher(self):
        file = publisher.prefix('ea') / 'drive_c' / publisher.PROVIDERS['ea']['clients'][0]
        file.parent.mkdir(parents=True)
        file.write_bytes(b'')
        self.assertIsNone(publisher.launcher('ea'))

    def test_ea_versioned_installation_uses_the_launcher_in_its_actual_folder(self):
        folder = publisher.prefix('ea') / 'drive_c/Program Files/Electronic Arts/EA Desktop/13.805.11.6320/EA Desktop'
        folder.mkdir(parents=True)
        (folder / 'EALauncher.exe').write_bytes(b'MZlauncher')
        self.assertIsNone(publisher.launcher('ea'))
        (folder / 'EADesktop.exe').write_bytes(b'MZdesktop')
        with patch.object(publisher, 'download') as download:
            self.assertEqual(publisher.ensure_launcher('ea', self.root / 'downloads'), folder / 'EALauncher.exe')
        download.assert_not_called()

    def test_ea_updates_choose_the_newest_complete_numeric_version(self):
        base = publisher.prefix('ea') / 'drive_c/Program Files/Electronic Arts/EA Desktop'
        for version in ('13.9.0', '13.10.0', '14.1.0', 'StagedEADesktop'):
            folder = base / version / 'EA Desktop'
            folder.mkdir(parents=True)
            (folder / 'EALauncher.exe').write_bytes(b'MZlauncher')
            if version != '14.1.0':
                (folder / 'EADesktop.exe').write_bytes(b'MZdesktop')
        self.assertEqual(publisher.launcher('ea'), base / '13.10.0/EA Desktop/EALauncher.exe')

    def test_ea_classic_layout_prefers_the_launcher_over_the_desktop_process(self):
        folder = publisher.prefix('ea') / 'drive_c/Program Files/Electronic Arts/EA Desktop/EA Desktop'
        folder.mkdir(parents=True)
        (folder / 'EADesktop.exe').write_bytes(b'MZdesktop')
        self.assertEqual(publisher.launcher('ea'), folder / 'EADesktop.exe')
        (folder / 'EALauncher.exe').write_bytes(b'MZlauncher')
        self.assertEqual(publisher.launcher('ea'), folder / 'EALauncher.exe')

    def test_ea_bootstrap_hands_off_to_the_detected_versioned_client(self):
        request = self.request(provider='ea')
        (publisher.prefix('ea') / 'drive_c' / publisher.PROVIDERS['ea']['games']).mkdir(parents=True)
        folder = publisher.prefix('ea') / 'drive_c/Program Files/Electronic Arts/EA Desktop/13.805.11.6320/EA Desktop'
        def install(*args, **kwargs):
            folder.mkdir(parents=True)
            (folder / 'EALauncher.exe').write_bytes(b'MZlauncher')
            (folder / 'EADesktop.exe').write_bytes(b'MZdesktop')
            child = Mock(returncode=0)
            child.poll.return_value = 0
            return child
        with patch.object(publisher, 'download'), patch.object(publisher.subprocess, 'Popen', side_effect=install) as setup, \
                patch.object(publisher, 'run_client', return_value=0) as client:
            self.assertEqual(publisher.run('ea', request), 0)
        self.assertIn('EAX_LAUNCH_CLIENT=0', setup.call_args.args[0])
        self.assertEqual(client.call_args.args[1], folder / 'EALauncher.exe')
        self.assertFalse((request / publisher.RESULT).exists())

    def test_unrelated_game_is_not_registered_for_the_requested_title(self):
        folder = self.request()
        self.ubisoft_game('Another Game')
        with patch.object(publisher, 'ensure_launcher', return_value=Path('/UbisoftConnect.exe')), \
                patch.object(publisher, 'run_client', return_value=0):
            publisher.run('ubisoft', folder)
        self.assertFalse((folder / publisher.RESULT).exists())

    def test_windows_registry_decoding_preserves_game_paths(self):
        sections = publisher.registry('[Software\\\\Ubisoft\\\\Launcher\\\\Installs\\\\42] 2\n"InstallDir"="C:\\\\Games\\\\Game"\n')
        self.assertEqual(sections['software\\ubisoft\\launcher\\installs\\42']['installdir'], 'C:\\Games\\Game')

    def test_path_traversal_and_non_windows_install_locations_are_rejected(self):
        pfx = publisher.prefix('ubisoft')
        for path in ('C:/../outside', '/tmp/outside', 'https://example.test/game'):
            self.assertIsNone(publisher.windows_path(path, pfx))
        self.assertEqual(publisher.windows_path('C:/Games/Game', pfx), pfx / 'drive_c/Games/Game')

    def test_ea_game_requires_installer_metadata_and_payload(self):
        pfx = publisher.prefix('ea')
        game = pfx / 'drive_c/Program Files/EA Games/It Takes Two'
        (game / '__Installer').mkdir(parents=True)
        (game / '__Installer/installerdata.xml').write_text(
            '<DiPManifest><contentIDs><contentID>Origin.OFR.50.0004262</contentID></contentIDs><gameTitle>It Takes Two</gameTitle></DiPManifest>')
        self.assertEqual(publisher.records('ea', self.root / 'downloads'), [])
        (game / 'ItTakesTwo.exe').write_bytes(b'MZpayload')
        data = publisher.records('ea', self.root / 'downloads')
        self.assertEqual(data[0]['offerId'], 'Origin.OFR.50.0004262')
        self.assertEqual(data[0]['title'], 'It Takes Two')

    def test_ea_installer_metadata_rejects_dtd_and_invalid_offer_ids(self):
        game = self.root / 'ea-game'
        (game / '__Installer').mkdir(parents=True)
        file = game / '__Installer/installerdata.xml'
        for xml in ('<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>',
                    '<x><contentID>bad id &amp; command</contentID></x>'):
            file.write_text(xml)
            self.assertIsNone(publisher.ea_metadata(game))

    def test_native_client_protocols_use_native_product_ids(self):
        self.assertEqual(publisher.launch_uri('ubisoft', '1803', install=True), 'uplay://install/1803')
        uri = publisher.launch_uri('ea', 'Origin.OFR.50.0004262')
        self.assertEqual(parse_qs(urlparse(uri).query), {'offerIds': ['Origin.OFR.50.0004262'], 'autoDownload': ['1']})
        for provider in ('ubisoft', 'ea'):
            with self.assertRaises(ValueError):
                publisher.launch_uri(provider, '1 & command')

    def test_each_provider_has_one_prefix_across_stores(self):
        self.assertEqual(publisher.prefix_id('ubisoft'), publisher.prefix_id('ubisoft'))
        self.assertNotEqual(publisher.prefix_id('ubisoft'), publisher.prefix_id('ea'))
        self.assertTrue(0 < int(publisher.prefix_id('ea')) <= 0xffffffff)

    def test_epic_link_handoff_uses_a_fresh_exchange_without_saving_it(self):
        ask = Mock(return_value=dict(ok=True, code='test-exchange', accountId='test-id', displayName='test name'))
        with patch.dict(os.environ, {'BL_LAUNCH_DIR': str(self.root)}), patch.object(publisher.runpy, 'run_path', return_value={'ask': ask}):
            uri = publisher.epic_uri('ea', dict(store='epic', id='EpicGame'))
        self.assertTrue(uri.startswith('link2ea://launchgame/EpicGame?'))
        self.assertEqual(parse_qs(urlparse(uri).query)['AUTH_PASSWORD'], ['test-exchange'])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_epic_ubisoft_handoff_can_use_the_partner_app_name_before_a_native_id_is_known(self):
        ask = Mock(return_value=dict(ok=True, code='test-exchange', accountId='test-id', displayName='test name'))
        with patch.dict(os.environ, {'BL_LAUNCH_DIR': str(self.root)}), patch.object(publisher.runpy, 'run_path', return_value={'ask': ask}):
            uri = publisher.epic_uri('ubisoft', dict(store='epic', id='EpicPartnerGame'))
        self.assertTrue(uri.startswith('uplay://launch/EpicPartnerGame?'))
        self.assertEqual(parse_qs(urlparse(uri).query)['AUTH_TYPE'], ['exchangecode'])

    def test_xbox_subscription_activation_uses_the_official_ubisoft_flow(self):
        ask = Mock(return_value={'ok': True})
        with patch.dict(os.environ, {'BL_LAUNCH_DIR': str(self.root)}), patch.object(publisher.runpy, 'run_path', return_value={'ask': ask}):
            publisher.activate_subscription('ubisoft', dict(store='xbox', subscription=True))
        self.assertEqual(ask.call_args.args[1], {'op': 'open-url', 'url': 'https://gamepass.ubisoft.com/'})

    def test_management_opens_client_without_launching_the_game(self):
        folder = self.request(action='manage')
        self.ubisoft_game()
        with patch.object(publisher, 'ensure_launcher', return_value=Path('/UbisoftConnect.exe')), \
                patch.object(publisher, 'run_client', return_value=0) as run:
            publisher.run('ubisoft', folder)
        self.assertEqual(run.call_args.args[2], [])

    def test_uninstall_result_is_emitted_only_after_client_no_longer_lists_the_game(self):
        folder = self.request(action='uninstall')
        with patch.object(publisher, 'ensure_launcher', return_value=Path('/UbisoftConnect.exe')), \
                patch.object(publisher, 'run_client', return_value=0):
            publisher.run('ubisoft', folder)
        self.assertTrue(json.loads((folder / publisher.RESULT).read_text())['removed'])

    def test_background_services_do_not_hold_the_session_after_the_client_closes(self):
        ticks = Mock()
        processes = Mock(side_effect=[[(1, ['EADesktop.exe'])], [(2, ['EABackgroundService.exe'])], [(2, ['EABackgroundService.exe'])]])
        tools = {'NOT_GAME': set(), 'wine_processes': processes, 'program': lambda argv: argv[0].lower(), 'is_game': lambda argv: True}
        child = Mock()
        child.poll.return_value = 0
        with patch.object(publisher.runpy, 'run_path', return_value=tools), patch.object(publisher.subprocess, 'Popen', return_value=child) as run, \
                patch.object(publisher.time, 'monotonic', side_effect=[0, 1, 2, 8]), patch.object(publisher.time, 'sleep'):
            self.assertEqual(publisher.run_client('ea', '/EADesktop.exe', [], ticks), 0)
        self.assertEqual(run.call_args.args[0][4], 'run')
        self.assertEqual(ticks.call_count, 3)

    def test_a_game_keeps_the_session_running_after_the_launcher_closes(self):
        processes = Mock(side_effect=[[(1, ['upc.exe'])], [(2, ['FarCry5.exe'])], [], []])
        tools = {'NOT_GAME': set(), 'wine_processes': processes, 'program': lambda argv: argv[0].lower(), 'is_game': lambda argv: True}
        child = Mock()
        child.poll.return_value = 0
        with patch.object(publisher.runpy, 'run_path', return_value=tools), patch.object(publisher.subprocess, 'Popen', return_value=child), \
                patch.object(publisher.time, 'monotonic', side_effect=[0, 1, 10, 20, 26]), patch.object(publisher.time, 'sleep'):
            self.assertEqual(publisher.run_client('ubisoft', '/upc.exe', [], lambda: None), 0)
        self.assertEqual(processes.call_count, 4)

    def test_publisher_run_reuses_ubisoft_fixes_without_recursive_registry_setup(self):
        game = runpy.run_path(str(BIN / 'droiddeck-game-env'))
        start = game['start']
        for verb, expected in (('run', 1), ('runinprefix', 0)):
            overlay, composition = Mock(), Mock()
            with patch.dict(start.__globals__, {'ubisoft_overlay_off': overlay, 'ubisoft_composition_off': composition,
                                                'system_graphics_launch': lambda launch, env: (launch, env)}), \
                    patch.object(os, 'execvpe', return_value=0):
                self.assertEqual(start(['/proton', verb, 'Client.exe'], {'DROIDDECK_PUBLISHER_ACTIVE': '1'}, None), 0)
            self.assertEqual(overlay.call_count, expected)
            self.assertEqual(composition.call_count, expected)

    def test_managed_games_dispatch_through_the_same_helper_for_every_store(self):
        for store in ('xbox', 'epic', 'gog', 'amazon'):
            sidecar = dict(store=store, id='Game', extra=dict(publisherManaged='1', publisherLauncher='ea'))
            with patch.object(publisher.runpy, 'run_path', return_value={'find_store_game': lambda argv: (self.root, sidecar)}), \
                    patch.object(publisher.subprocess, 'call', return_value=0) as run:
                self.assertEqual(publisher.dispatch(['/proton', 'waitforexitandrun', '/game.exe'], {}), 0)
            self.assertEqual(run.call_args.args[0][-3:], ['ea', '--request', str(self.root)])

    def test_game_environment_loads_publisher_support_only_for_its_shortcuts(self):
        game = runpy.run_path(str(BIN / 'droiddeck-game-env'))
        with patch.object(runpy, 'run_path', side_effect=OSError('optional helper unavailable')) as load:
            self.assertIsNone(game['publisher_launch'](['/stock', 'run'], {}))
            self.assertIsNone(game['publisher_launch'](['/proton', 'waitforexitandrun', '/Game.exe'], {}))
            self.assertIsNone(game['publisher_launch'](['/proton', 'run', '/.droiddeck-publisher-launch.bat'],
                                                        {'DROIDDECK_PUBLISHER_ACTIVE': '1'}))
            load.assert_not_called()

    def test_game_environment_dispatches_managed_shortcuts_and_preserves_status(self):
        game = runpy.run_path(str(BIN / 'droiddeck-game-env'))
        dispatch = Mock(return_value=23)
        command = ['/proton', 'waitforexitandrun', str(self.root / '.droiddeck-publisher-launch.bat')]
        env = {'CUSTOM': 'kept'}
        with patch.object(runpy, 'run_path', return_value={'dispatch': dispatch}):
            self.assertEqual(game['publisher_launch'](command, env), 23)
        dispatch.assert_called_once_with(command, env)

    def test_managed_shortcuts_do_not_fall_back_when_the_publisher_helper_is_missing(self):
        game = runpy.run_path(str(BIN / 'droiddeck-game-env'))
        with patch.object(runpy, 'run_path', side_effect=OSError('publisher helper unavailable')):
            with self.assertRaises(OSError):
                game['publisher_launch'](['/proton', 'run', '/.droiddeck-publisher-launch.bat'], {})

    def test_store_preparation_routes_managed_games_before_store_specific_licensing(self):
        source = importlib.machinery.SourceFileLoader('publisher_store_launch', str(BIN / 'droiddeck-store-launch'))
        specification = importlib.util.spec_from_loader(source.name, source)
        store_launch = importlib.util.module_from_spec(specification)
        source.exec_module(store_launch)
        bat = self.root / '.droiddeck-publisher-launch.bat'
        bat.write_text('@echo off')
        for store in ('xbox', 'epic', 'gog', 'amazon'):
            sidecar = dict(store=store, id='Game', extra=dict(publisherManaged='1', publisherLauncher='ubisoft'))
            (self.root / '.droiddeck-store.json').write_text(json.dumps(sidecar))
            with patch.object(store_launch.subprocess, 'call', return_value=0) as run:
                self.assertEqual(store_launch.main([str(bat)]), store_launch.RAN)
            self.assertEqual(run.call_args.args[0][-3:], ['ubisoft', '--request', str(self.root)])

    def test_ordinary_games_and_client_children_are_not_intercepted(self):
        with patch.object(publisher.runpy, 'run_path', return_value={'find_store_game': lambda argv: (self.root, dict(store='epic'))}), \
                patch.object(publisher.subprocess, 'call') as run:
            self.assertIsNone(publisher.dispatch(['/Game.exe'], {}))
            self.assertIsNone(publisher.dispatch(['/Game.exe'], {'DROIDDECK_PUBLISHER_ACTIVE': '1'}))
            run.assert_not_called()

    def test_download_rejects_html_and_removes_incomplete_installer(self):
        source = io.BytesIO(b'<html>server error</html>')
        source.geturl = lambda: 'https://example.test/setup.exe'
        target = self.root / 'setup.exe'
        with patch.object(publisher.urllib.request, 'urlopen', return_value=source):
            with self.assertRaises(ValueError):
                publisher.download('https://example.test/setup.exe', target)
        self.assertFalse(target.exists())
        self.assertFalse(target.with_suffix('.part').exists())


if __name__ == '__main__':
    unittest.main()
