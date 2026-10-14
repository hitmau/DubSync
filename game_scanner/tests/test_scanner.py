from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from game_scanner.core.scanner import run_scan
from game_scanner.reports.exporter import export_json


class ScannerIntegrationTests(unittest.TestCase):
    def make_replaced_sample(self, root: Path) -> Path:
        data = root / "Replaced_Data" / "il2cpp_data" / "Metadata"
        data.mkdir(parents=True)
        (root / "Replaced.exe").write_bytes(b"MZ")
        (root / "GameAssembly.dll").write_bytes(b"DialogueFragment ExternalId InitLine ReleaseLine FMOD")
        (root / "UnityPlayer.dll").write_bytes(b"MZ")
        (data / "global-metadata.dat").write_bytes(
            b"Articy DialogueFragment ExternalId ShortId SubtitlesManager InitLine ReleaseLine"
        )
        core = root / "BepInEx" / "core"
        core.mkdir(parents=True)
        (core / "BepInEx.Core.dll").write_bytes(b"BepInEx loader marker")
        (root / "BepInEx" / "LogOutput.log").write_text("log output", encoding="utf-8")
        (root / "Replaced_Data" / "Plugins" / "x86_64").mkdir(parents=True)
        (root / "Replaced_Data" / "Plugins" / "x86_64" / "fmodstudio.dll").write_bytes(b"FMOD")
        return root / "Replaced.exe"

    def test_scan_recognizes_known_signatures_but_keeps_runtime_pending(self):
        with tempfile.TemporaryDirectory() as temp:
            target = self.make_replaced_sample(Path(temp))
            before = sorted(path.relative_to(target.parent).as_posix()
                            for path in target.parent.rglob("*") if path.is_file())
            report = run_scan(target)
            after = sorted(path.relative_to(target.parent).as_posix()
                           for path in target.parent.rglob("*") if path.is_file())

            self.assertEqual(before, after, "o scan não deve criar ou alterar arquivos do jogo")
            self.assertEqual(8, len(report.findings))
            findings = {item.layer: item for item in report.findings}
            self.assertEqual("detected", findings["engine"].status)
            self.assertIn("IL2CPP", findings["build_runtime"].summary)
            self.assertEqual("detected", findings["dialogue"].status)
            self.assertEqual("probable", findings["runtime_events"].status)
            self.assertTrue(findings["runtime_events"].runtime_validation_required)
            self.assertEqual("detected", findings["audio"].status)
            self.assertEqual("detected", findings["modding"].status)
            modding_paths = [item.path for item in findings["modding"].evidence]
            self.assertFalse(any(path.endswith(".log") for path in modding_paths))
            self.assertEqual("replaced.articy", report.recommendation["adapter_candidate"])
            plugin = report.recommendation["registered_plugin_candidates"][0]
            self.assertTrue(plugin["runtime_callback_observed"])
            self.assertFalse(plugin["runtime_hook_confirmed"])

    def test_json_export_contains_all_eight_layers(self):
        with tempfile.TemporaryDirectory() as temp:
            target = self.make_replaced_sample(Path(temp))
            report = run_scan(target)
            destination = export_json(report, Path(temp) / "report.json")
            exported = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(1, exported["schema_version"])
            self.assertEqual(8, len(exported["diagnostics"]))
            self.assertTrue(exported["recommendation"]["static_analysis_is_not_runtime_confirmation"])


if __name__ == "__main__":
    unittest.main()

