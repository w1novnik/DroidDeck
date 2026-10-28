import contextlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

BIN = Path(__file__).resolve().parents[1] / "linuxfs/overlay/usr/local/bin"
G = runpy.run_path(str(BIN / "droiddeck-gog-maintenance"))["main"].__globals__


class GogMaintenance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.control = G["control"](self.folder)
        (self.folder / ".droiddeck-store.json").write_text(json.dumps({"store": "gog", "id": "1"}))

    def test_only_downloaded_windows_executables_can_be_patches(self):
        download = self.control / "downloads"
        download.mkdir()
        patch = download / "patch.exe"
        patch.write_bytes(b"MZtest")
        self.assertEqual(patch, G["validate_installer"](self.folder, patch))
        outside = self.folder / "outside.exe"
        outside.write_bytes(b"MZtest")
        with self.assertRaises(ValueError):
            G["validate_installer"](self.folder, outside)
        patch.write_bytes(b"not PE")
        with self.assertRaises(ValueError):
            G["validate_installer"](self.folder, patch)

    def test_only_recognized_inno_installers_get_the_gog_ui_compatibility_argument(self):
        path = self.folder / "patch.exe"
        path.write_bytes(b"MZunknown installer")
        self.assertEqual([], G["installer_arguments"](path))
        path.write_bytes(b"MZdata Inno Setup Setup Data (6.2.1)")
        self.assertEqual(["/NOGUI"], G["installer_arguments"](path))

    def test_game_launch_lease_excludes_updater_and_releases_on_owner_exit(self):
        import fcntl
        owner = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        self.addCleanup(lambda: owner.poll() is None and owner.kill())
        lease = subprocess.Popen([sys.executable, str(BIN / "droiddeck-gog-maintenance"), "lease", str(self.folder), str(owner.pid)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(lease.stdout.close)
        self.addCleanup(lease.stderr.close)
        self.addCleanup(lambda: lease.poll() is None and lease.kill())
        self.assertEqual(b"ready\n", lease.stdout.readline())
        with G["lock_file"](self.control) as stream:
            with self.assertRaises(BlockingIOError):
                fcntl.lockf(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        owner.terminate(); owner.wait()
        lease.wait(timeout=5)
        with G["lock_file"](self.control) as stream:
            fcntl.lockf(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_incomplete_update_cannot_launch(self):
        (self.folder / ".droiddeck-store.json").write_text(json.dumps({"store": "gog", "id": "1", "state": "updating"}))
        with self.assertRaises(ValueError):
            G["lease"](self.folder, os.getpid())

    def test_running_game_and_wrong_prefix_are_distinguished(self):
        proc = self.folder / "proc"
        for pid, prefix, name in [(10, "/prefix/pfx", "game.exe"), (11, "/other/pfx", "other.exe"), (12, "/prefix/pfx", "services.exe")]:
            row = proc / str(pid); row.mkdir(parents=True)
            (row / "environ").write_bytes(b"WINEPREFIX=" + prefix.encode() + b"\0")
            (row / "cmdline").write_bytes(name.encode() + b"\0")
        self.assertEqual([10], G["active"]("/prefix", proc))

    def test_desktop_installer_inherits_the_nested_display_not_the_bridge_display(self):
        proc = self.folder / "proc"
        row = proc / "10"; row.mkdir(parents=True)
        (row / "cmdline").write_bytes(b"/usr/bin/plasmashell\0")
        (row / "environ").write_bytes(b"BL_DESKTOP=1\0XDG_RUNTIME_DIR=/run/session\0WAYLAND_DISPLAY=wayland-1\0DISPLAY=:0\0XAUTHORITY=/run/session/auth\0UNRELATED=value\0")
        env = G["desktop_environment"]({"DROIDDECK_GOG_DESKTOP": "1", "XDG_RUNTIME_DIR": "/run/session", "WAYLAND_DISPLAY": "wayland-0"}, proc)
        self.assertEqual("wayland-1", env["WAYLAND_DISPLAY"])
        self.assertEqual(":0", env["DISPLAY"])
        self.assertEqual("/run/session/auth", env["XAUTHORITY"])
        self.assertEqual("1", env["BL_DESKTOP"])
        self.assertNotIn("UNRELATED", env)

    def test_legacy_setup_uses_authentic_timestamp_and_support_layout(self):
        program = self.folder / "support/1/setup.exe"
        program.parent.mkdir(parents=True); program.write_bytes(b"MZtest")
        record = {"id": "1", "build": "3301", "products": ["1"], "language": "en-US", "manifest": {"product": {
            "timestamp": 31847977, "support_commands": [{"gameID": "1", "executable": "/setup.exe", "languages": ["English"]}]}}}
        G["write"](self.control / "installed.json", {"verified": False})
        G["write"](self.control / "operation.json", {"before": record})
        G["write"](self.folder / "goggame-1.info", {"language": "English"})
        with mock.patch.object(G["subprocess"], "run", return_value=mock.Mock(returncode=0)) as run:
            G["prepare_setup"](str(self.folder), "/proton", {})
        args = run.call_args.args[0]
        self.assertEqual(str(program), args[2])
        self.assertIn("/buildId=31847977", args)
        self.assertNotIn("/buildId=3301", args)
        self.assertIn("/Language=English", args)

    def test_interrupted_patch_is_not_started_again(self):
        (self.control / "operation.json").write_text(json.dumps({"phase": "patch-applying"}))
        with mock.patch.dict(G, {"validate_installer": lambda *a: self.folder / "patch.exe"}):
            with self.assertRaises(ValueError):
                G["start"](str(self.folder), "2147483666", "patch.exe", "game.exe")

    def test_dead_installer_helper_requires_recovery_without_replay(self):
        G["write"](self.control / "operation.json", {"phase": "patch-applying"})
        G["write"](self.control / "patch-status.json", {"state": "running", "pid": 99999999, "generation": "123"})
        with mock.patch.dict(G, {"generation": lambda pid: None}), mock.patch.object(G["subprocess"], "Popen") as spawn:
            self.assertEqual(0, G["status"](str(self.folder)))
        self.assertFalse(spawn.called)
        self.assertEqual("uncertain", G["read"](self.control / "patch-status.json")["state"])
        self.assertEqual("patch-uncertain", G["read"](self.control / "operation.json")["phase"])

    def test_abandoned_start_and_confirmed_completion_are_distinct(self):
        G["write"](self.control / "operation.json", {"phase": "patch-starting"})
        G["write"](self.control / "patch-status.json", {"state": "starting", "startedAt": 10})
        with mock.patch.object(G["time"], "time", return_value=100):
            G["status"](str(self.folder))
        self.assertEqual("uncertain", G["read"](self.control / "patch-status.json")["state"])
        G["write"](self.control / "patch-status.json", {"state": "done", "exitCode": 0})
        G["status"](str(self.folder))
        self.assertEqual({"state": "done", "exitCode": 0}, G["read"](self.control / "patch-status.json"))

    def test_registry_and_installer_use_same_prefix_without_cloud_launch(self):
        download = self.control / "downloads"; download.mkdir()
        patch = download / "patch.exe"; patch.write_bytes(b"MZtest")
        prefix = self.folder / "prefix/pfx"
        (prefix / "drive_c/windows/system32").mkdir(parents=True)
        (prefix / "drive_c/windows/system32/reg.exe").write_bytes(b"MZreg")
        (prefix / "system.reg").write_text("registry")
        G["write"](self.control / "operation.json", {"phase": "patch-starting"})
        calls = []
        setup = {"main": lambda: None}
        def setup_main():
            pass
        setup_main.__globals__["apply_gog_registry"] = lambda *a, **k: calls.append(("registry", a[1])) or "written=1"
        fake = mock.Mock(); fake.wait.return_value = 0
        def spawn(argv, **kwargs):
            self.assertEqual("run", argv[1])
            self.assertEqual("1", kwargs["env"]["DROIDDECK_GOG_MAINTENANCE"])
            calls.append(("patch", kwargs["env"]["STEAM_COMPAT_DATA_PATH"]))
            return fake
        with mock.patch.dict(G, {"runner": lambda *a: ("/proton", {"STEAM_COMPAT_DATA_PATH": str(self.folder / "prefix"), "DROIDDECK_GOG_MAINTENANCE": "1"}, str(self.folder / "prefix")),
                                 "active": lambda *a: []}), \
             mock.patch.object(G["runpy"], "run_path", return_value={"main": setup_main}), \
             mock.patch.object(G["subprocess"], "Popen", side_effect=spawn):
            self.assertEqual(0, G["run_patch"](str(self.folder), "2147483666", str(patch), "game.exe"))
        self.assertEqual([("registry", str(self.folder / "prefix")), ("patch", str(self.folder / "prefix"))], calls)
        self.assertEqual("done", G["read"](self.control / "patch-status.json")["state"])
        self.assertEqual("updating", G["read"](self.folder / ".droiddeck-store.json")["state"])


if __name__ == "__main__":
    unittest.main()
