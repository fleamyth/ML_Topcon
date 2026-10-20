import importlib.util
import configparser
import csv
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
import os
import xml.etree.ElementTree as ET
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parent / "wait_for_solos_pair.py"
SPEC = importlib.util.spec_from_file_location("wait_for_solos_pair", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
wfsp = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wfsp)


XML_TEMPLATE = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Ophthalmology xmlns:nsCommon="http://www.joia.or.jp/standardized/namespaces/Common"'
    ' xmlns:nsLM="http://www.joia.or.jp/standardized/namespaces/LM">\n'
    '  <nsCommon:Common><nsCommon:Patient>'
    '<nsCommon:ID>{sn}</nsCommon:ID></nsCommon:Patient></nsCommon:Common>\n'
    '  <nsLM:Measure><nsLM:LM></nsLM:LM></nsLM:Measure>\n'
    '</Ophthalmology>\n'
)

PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer\n%%EOF\n"

PATTERN = "*_TOPCON_SOLOS_00000.xml"
SUFFIX = "_Spectacles_Report.pdf"


def measurement_xml(sn, fail=False, visible=89):
    common = (
        f"<Common><Patient><ID>{sn}</ID></Patient><Date>2026-10-19</Date>"
        "<Time>08:59:16</Time><ModelName>SOLOS</ModelName><MachineNo>00000</MachineNo></Common>"
    )
    eyes = ""
    for eye in ("R", "L"):
        spectrum = "".join(
            f"<TransmittanceValue><WaveLength>{wavelength}</WaveLength>"
            f"<Transmittance>{visible}</Transmittance></TransmittanceValue>"
            for wavelength in range(315, 801)
        )
        eyes += (
            f"<{eye}><Sphere>{'1.0' if fail else '0.0'}</Sphere><Cylinder>0</Cylinder>"
            "<PrismX/><PrismY/><UVTransmittance>0</UVTransmittance>"
            f"<VisibleTransmittance>{visible}</VisibleTransmittance>"
            f"<TransmittanceData>{spectrum}</TransmittanceData></{eye}>"
        )
    return f"<Ophthalmology>{common}<Measure><LM>{eyes}</LM></Measure></Ophthalmology>"


class PairWatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def write_pair(self, sn="SAA671D0000H", date="20261019", tm="085916",
                   xml=True, pdf=True, pdf_bytes=PDF_BYTES, age=1000):
        prefix = f"{sn}_{date}_{tm}"
        xml_path = self.dir / f"{prefix}_TOPCON_SOLOS_00000.xml"
        pdf_path = self.dir / f"{prefix}_Spectacles_Report.pdf"
        if xml:
            xml_path.write_text(measurement_xml(sn), encoding="utf-8")
        if pdf:
            pdf_path.write_bytes(pdf_bytes)
        if age:
            old = os.path.getmtime(self.dir) - age
            for p in (xml_path, pdf_path):
                if p.exists():
                    os.utime(p, (old, old))
        return xml_path, pdf_path

    def run_check(self, *extra):
        return wfsp.main(["--dir", str(self.dir), "--check-once", "--stable", "0", *extra])

    def find(self, excluded=None, use_existing=True, stable=0):
        return wfsp.find_ready_pair(self.dir, PATTERN, SUFFIX, stable,
                                    excluded or set(), use_existing)

    def test_finds_complete_pair(self):
        self.write_pair()
        self.assertEqual(self.run_check("--use-existing"), wfsp.EXIT_FOUND)

    def test_reports_patient_id_from_xml(self):
        self.write_pair(sn="ABCDE12345")
        found = self.find()
        self.assertIsNotNone(found)
        self.assertEqual(found[2], "ABCDE12345")

    def test_missing_pdf_is_not_ready(self):
        self.write_pair(pdf=False)
        self.assertEqual(self.run_check("--use-existing"), wfsp.EXIT_NOT_YET)

    def test_pdf_without_eof_is_not_ready(self):
        self.write_pair(pdf_bytes=b"%PDF-1.4\ntruncated")
        self.assertEqual(self.run_check("--use-existing"), wfsp.EXIT_NOT_YET)

    def test_malformed_xml_is_not_ready(self):
        prefix = "SAA671D0000H_20261019_085916"
        (self.dir / f"{prefix}_TOPCON_SOLOS_00000.xml").write_text("<broken", encoding="utf-8")
        (self.dir / f"{prefix}_Spectacles_Report.pdf").write_bytes(PDF_BYTES)
        old = os.path.getmtime(self.dir) - 1000
        for p in self.dir.iterdir():
            os.utime(p, (old, old))
        self.assertEqual(self.run_check("--use-existing"), wfsp.EXIT_NOT_YET)

    def test_unstable_file_is_not_ready(self):
        self.write_pair(age=0)
        self.assertEqual(self.run_check("--use-existing", "--stable", "3"),
                         wfsp.EXIT_NOT_YET)

    def test_old_timestamp_does_not_bypass_observation(self):
        self.write_pair()
        self.assertEqual(self.run_check("--use-existing", "--stable", "5"),
                         wfsp.EXIT_NOT_YET)

    def test_valid_xml_without_measurements_is_not_complete(self):
        xml, _ = self.write_pair()
        xml.write_text(XML_TEMPLATE.format(sn="UNIT123"), encoding="utf-8")
        self.assertFalse(wfsp.xml_is_complete(xml))

    def test_size_change_resets_window_even_with_old_timestamp(self):
        for path in self.write_pair():
            with self.subTest(file=path.name):
                tracker = wfsp.FileStability(5)
                original = path.stat()
                with patch.object(wfsp.time, "monotonic", side_effect=[0, 4, 8, 9]):
                    self.assertFalse(tracker.observe(path))
                    with path.open("ab") as handle:
                        handle.write(b"\n")
                    os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
                    self.assertFalse(tracker.observe(path))
                    self.assertFalse(tracker.observe(path))
                    self.assertTrue(tracker.observe(path))

    def test_same_size_timestamp_change_resets_window(self):
        xml, _ = self.write_pair()
        tracker = wfsp.FileStability(5)
        with patch.object(wfsp.time, "monotonic", side_effect=[0, 4, 9]):
            self.assertFalse(tracker.observe(xml))
            status = xml.stat()
            os.utime(xml, ns=(status.st_atime_ns, status.st_mtime_ns + 1_000_000_000))
            self.assertFalse(tracker.observe(xml))
            self.assertTrue(tracker.observe(xml))

    def test_disappearing_file_restarts_observation(self):
        xml, _ = self.write_pair()
        content = xml.read_bytes()
        tracker = wfsp.FileStability(5)
        with patch.object(wfsp.time, "monotonic", side_effect=[0, 5, 9, 10]):
            self.assertFalse(tracker.observe(xml))
            xml.unlink()
            self.assertFalse(tracker.observe(xml))
            xml.write_bytes(content)
            self.assertFalse(tracker.observe(xml))
            self.assertFalse(tracker.observe(xml))
            self.assertTrue(tracker.observe(xml))

    def test_write_during_validation_is_not_accepted(self):
        xml, pdf = self.write_pair()
        tracker = wfsp.FileStability(5)

        def find():
            return wfsp.find_ready_pair(self.dir, PATTERN, SUFFIX, 5, set(), True, tracker)

        with patch.object(wfsp.time, "monotonic", return_value=0):
            self.assertIsNone(find())
        original_validate = wfsp.xml_is_complete

        def validate_and_write(path):
            valid = original_validate(path)
            with pdf.open("ab") as handle:
                handle.write(b"\n")
            return valid

        with patch.object(wfsp.time, "monotonic", return_value=5):
            with patch.object(wfsp, "xml_is_complete", side_effect=validate_and_write):
                self.assertIsNone(find())
        with patch.object(wfsp.time, "monotonic", return_value=10):
            self.assertIsNotNone(find())

    def test_polling_waits_for_last_write_not_original_mtime(self):
        xml, _ = self.write_pair()
        clock = {"now": 0.0}
        original = xml.stat()

        def advance(seconds):
            clock["now"] += seconds
            if clock["now"] in (2, 4):
                with xml.open("ab") as handle:
                    handle.write(b"\n")
                os.utime(xml, ns=(original.st_atime_ns, original.st_mtime_ns))

        with patch.object(wfsp.time, "monotonic", side_effect=lambda: clock["now"]):
            with patch.object(wfsp.time, "sleep", side_effect=advance):
                result = wfsp.main(["--dir", str(self.dir), "--use-existing",
                                    "--stable", "5", "--poll", "1", "--timeout", "20"])
        self.assertEqual(result, wfsp.EXIT_FOUND)
        self.assertEqual(clock["now"], 9)

    def test_required_xml_content_is_validated(self):
        changes = {
            "serial": ("Common/Patient/ID", ""),
            "date": ("Common/Date", "invalid"),
            "sphere": ("Measure/LM/R/Sphere", ""),
            "cylinder": ("Measure/LM/L/Cylinder", "NaN"),
            "visible": ("Measure/LM/R/VisibleTransmittance", "inf"),
            "uv": ("Measure/LM/L/UVTransmittance", "missing"),
            "prism": ("Measure/LM/R/PrismX", "invalid"),
            "spectrum value": ("Measure/LM/R/TransmittanceData/TransmittanceValue/Transmittance", ""),
            "duplicate wavelength": ("Measure/LM/L/TransmittanceData/TransmittanceValue/WaveLength", "316"),
        }
        xml, _ = self.write_pair()
        for name, (location, value) in changes.items():
            with self.subTest(field=name):
                root = ET.fromstring(measurement_xml("UNIT123"))
                root.find(location).text = value
                ET.ElementTree(root).write(xml, encoding="utf-8")
                self.assertFalse(wfsp.xml_is_complete(xml))
                self.assertIsNone(self.find())
        for location in ("Measure/LM/L", "Measure/LM/R/PrismX",
                         "Measure/LM/R/TransmittanceData/TransmittanceValue"):
            with self.subTest(missing=location):
                root = ET.fromstring(measurement_xml("UNIT123"))
                parent, name = location.rsplit("/", 1)
                root.find(parent).remove(root.find(location))
                ET.ElementTree(root).write(xml, encoding="utf-8")
                self.assertFalse(wfsp.xml_is_complete(xml))

    def test_namespaced_xml_and_blank_prisms_are_supported(self):
        xml, _ = self.write_pair()
        root = ET.fromstring(measurement_xml("UNIT123"))
        for element in root.iter():
            element.tag = "{http://www.joia.or.jp/standardized/namespaces/LM}" + element.tag
        ET.ElementTree(root).write(xml, encoding="utf-8")
        self.assertTrue(wfsp.xml_is_complete(xml))

    def test_newest_pair_wins(self):
        self.write_pair(sn="OLD00000001", tm="010101", age=5000)
        self.write_pair(sn="NEW00000002", tm="090000", age=1000)
        found = self.find()
        self.assertEqual(found[2], "NEW00000002")

    def test_baseline_excludes_handled_file(self):
        xml, _ = self.write_pair()
        found = self.find(excluded={xml.name}, use_existing=False)
        self.assertIsNone(found)

    def test_new_file_not_in_baseline_is_found(self):
        self.write_pair(sn="OLD00000001", tm="010101", age=5000)
        self.write_pair(sn="NEW00000002", tm="090000", age=1000)
        old_name = "OLD00000001_20261019_010101_TOPCON_SOLOS_00000.xml"
        found = self.find(excluded={old_name}, use_existing=False)
        self.assertIsNotNone(found)
        self.assertEqual(found[2], "NEW00000002")

    def test_diagnose_reports_missing_pdf(self):
        self.write_pair(pdf=False)
        reason = wfsp.diagnose(self.dir, PATTERN, SUFFIX, 1, set(), True)
        self.assertIn("Spectacles_Report.pdf", reason)

    def test_print_config(self):
        rc = wfsp.main(["--dir", str(self.dir), "--print-config"])
        self.assertEqual(rc, wfsp.EXIT_FOUND)

    def test_archive_preserves_pair_and_removes_sources(self):
        xml, pdf = self.write_pair()
        original_xml, original_pdf = xml.read_bytes(), pdf.read_bytes()
        destination = wfsp.archive_pair(xml, pdf, self.dir / "archive")
        self.assertEqual((destination / xml.name).read_bytes(), original_xml)
        self.assertEqual((destination / pdf.name).read_bytes(), original_pdf)
        self.assertFalse(xml.exists())
        self.assertFalse(pdf.exists())
        self.assertIsNone(self.find())

    def test_archive_never_overwrites_previous_pair(self):
        xml, pdf = self.write_pair()
        first = wfsp.archive_pair(xml, pdf, self.dir / "archive")
        xml, pdf = self.write_pair()
        second = wfsp.archive_pair(xml, pdf, self.dir / "archive")
        self.assertNotEqual(first, second)
        self.assertTrue((first / xml.name).is_file())
        self.assertTrue((second / xml.name).is_file())

    def test_archive_copy_failure_keeps_both_sources(self):
        xml, pdf = self.write_pair()
        original_copy = wfsp.shutil.copy2

        def copy_or_fail(source, destination):
            if source == pdf:
                raise OSError("PDF copy failed")
            return original_copy(source, destination)

        with patch.object(wfsp.shutil, "copy2", side_effect=copy_or_fail):
            with self.assertRaises(OSError):
                wfsp.archive_pair(xml, pdf, self.dir / "archive")
        self.assertTrue(xml.is_file())
        self.assertTrue(pdf.is_file())

    def test_deferred_pair_is_skipped_until_either_file_changes(self):
        for changed in ("xml", "pdf"):
            with self.subTest(changed=changed):
                xml, pdf = self.write_pair()
                state_path = self.dir / "deferred.json"
                self.assertEqual(wfsp.main([
                    "--deferred", str(state_path), "--defer-pair", str(xml), str(pdf),
                ]), 0)
                state = wfsp.read_deferred(state_path)
                self.assertIsNone(wfsp.find_ready_pair(
                    self.dir, PATTERN, SUFFIX, 0, set(), True, deferred=state,
                ))
                path = xml if changed == "xml" else pdf
                with path.open("ab") as handle:
                    handle.write(b"\n")
                self.assertIsNotNone(wfsp.find_ready_pair(
                    self.dir, PATTERN, SUFFIX, 0, set(), True, deferred=state,
                ))


@unittest.skipUnless(os.name == "nt", "Windows BAT integration")
class BatFlowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="solos offline test ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        repository = MODULE_PATH.parents[1]
        for name in ("run_solos_test.bat", "solos_xml_to_pcbafct.py",
                     "tools/wait_for_solos_pair.py", "RESTSFIS-diag/RESTSFIS-diag.py"):
            destination = self.root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(repository / name, destination)
        self.config = configparser.ConfigParser()
        self.config["TEST"] = {
            "stable_seconds": "0", "wait_timeout": "0.2", "poll_seconds": "0.1",
        }
        self.config["SCREEN"] = {"enabled": "0"}
        with (self.root / "RESTSFIS-diag/RESTSFIS-diag.ini").open("w") as handle:
            handle.write("[SFIS]\nbase_url = http://example.invalid\nprocess = Lensometer\n")

    def write_config(self):
        with (self.root / "solos_test.ini").open("w", encoding="utf-8") as handle:
            self.config.write(handle)

    def write_pair(self, fail=False, sn="UNIT123", visible=89):
        xml = self.root / f"{sn}_20261019_085916_TOPCON_SOLOS_00000.xml"
        pdf = self.root / f"{sn}_20261019_085916_Spectacles_Report.pdf"
        xml.write_text(measurement_xml(sn, fail, visible), encoding="utf-8")
        pdf.write_bytes(PDF_BYTES)
        return xml, pdf

    def test_sunlens_sn_automatically_uses_d15_from_xml_id(self):
        xml, pdf = self.write_pair(sn="SAA671F0001M", visible=20)
        xml.rename(self.root / xml.name.replace("SAA671F", "SAA671D"))
        pdf.rename(self.root / pdf.name.replace("SAA671F", "SAA671D"))
        result = self.run_bat("--once", "--use-existing", "--no-sfis")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("D15", result.stdout)
        self.assertIn("PASS=17, FAIL=0, N/A=0", result.stdout)
        with (self.root / "output/SAA671F0001M/Lensometer.csv").open(
            encoding="utf-8-sig", newline="",
        ) as handle:
            rows = list(csv.DictReader(handle))
        visible = [row for row in rows if (row["LowerLimit"], row["UpperLimit"]) == ("8", "40")]
        self.assertEqual(len(visible), 2)
        self.assertEqual({row["Value"] for row in visible}, {"20"})
        self.assertTrue(any((self.root / "archive").glob("*/Lensometer.csv")))

    def test_mixed_batch_selects_configuration_per_xml(self):
        sun_xml, _ = self.write_pair(sn="SAA671F0001M", visible=20)
        clear_xml, _ = self.write_pair(sn="SAA671D0001M")
        result = subprocess.run(
            ["python", "solos_xml_to_pcbafct.py", str(sun_xml), str(clear_xml), "-o", "batch"],
            cwd=self.root, capture_output=True, encoding="utf-8", errors="replace", timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.count("PASS=17, FAIL=0, N/A=0"), 2)
        for sn, limits in (("SAA671F0001M", ("8", "40")), ("SAA671D0001M", ("40", ""))):
            with self.subTest(sn=sn):
                with (self.root / f"batch/{sn}/Lensometer.csv").open(
                    encoding="utf-8-sig", newline="",
                ) as handle:
                    rows = list(csv.DictReader(handle))
                self.assertEqual(sum((row["LowerLimit"], row["UpperLimit"]) == limits
                                     for row in rows), 2)

    def test_only_hw_id_1f_uses_sunlens_limits(self):
        hw_ids = ("1H", "1B", "1C", "1D", "1E", "1F", "1G")
        inputs = []
        for hw_id in hw_ids:
            sn = f"SAA67{hw_id}0001F"
            xml = self.write_pair(sn=sn, visible=20 if hw_id == "1F" else 89)[0]
            inputs.append(str(xml))
        result = subprocess.run(
            ["python", "solos_xml_to_pcbafct.py", *inputs, "-o", "batch"],
            cwd=self.root, capture_output=True, encoding="utf-8", errors="replace", timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.count("PASS=17, FAIL=0, N/A=0"), len(hw_ids))
        for hw_id in hw_ids:
            with self.subTest(hw_id=hw_id):
                sn = f"SAA67{hw_id}0001F"
                with (self.root / f"batch/{sn}/Lensometer.csv").open(
                    encoding="utf-8-sig", newline="",
                ) as handle:
                    rows = list(csv.DictReader(handle))
                limits = ("8", "40") if hw_id == "1F" else ("40", "")
                self.assertEqual(sum((row["LowerLimit"], row["UpperLimit"]) == limits
                                     for row in rows), 2)

    def test_explicit_config_overrides_automatic_sn_selection(self):
        xml, _ = self.write_pair(sn="SAA671F0001M", visible=20)
        result = subprocess.run(
            ["python", "solos_xml_to_pcbafct.py", str(xml), "--config", "D10", "-o", "override.csv"],
            cwd=self.root, capture_output=True, encoding="utf-8", errors="replace", timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("D10 Plano", result.stdout)
        self.assertIn("FAIL=2,", result.stdout)

    def run_bat(self, *arguments):
        self.write_config()
        return subprocess.run(
            ["cmd.exe", "/d", "/c", "run_solos_test.bat", "--no-screen", *arguments],
            cwd=self.root, capture_output=True, encoding="utf-8", errors="replace",
            timeout=15,
        )

    def run_until_waiting_again(self, *arguments, waiting_count=2):
        self.write_config()
        process = subprocess.Popen(
            ["cmd.exe", "/d", "/c", "run_solos_test.bat", "--no-screen", *arguments],
            cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            encoding="utf-8", errors="replace",
        )
        output = []
        returned = threading.Event()

        def collect():
            waits = 0
            for line in process.stdout:
                output.append(line)
                if "Waiting for SOLOS XML/PDF" in line:
                    waits += 1
                    if waits >= waiting_count:
                        returned.set()

        reader = threading.Thread(target=collect, daemon=True)
        reader.start()
        try:
            self.assertTrue(returned.wait(10), "".join(output))
        finally:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           capture_output=True)
            process.wait(timeout=5)
            reader.join(timeout=5)
            process.stdout.close()
        return "".join(output)

    def test_continuous_mode_returns_to_wait_after_timeout(self):
        output = self.run_until_waiting_again("--no-sfis")
        self.assertIn("timed out", output)

    def test_continuous_mode_returns_to_wait_after_sfis_failure(self):
        self.write_pair()
        self.config["TEST"]["retry"] = "0"
        self.config["TEST"]["sfis_script"] = "sfis_stub.py"
        (self.root / "sfis_stub.py").write_text("raise SystemExit(11)\n", encoding="utf-8")
        output = self.run_until_waiting_again("--use-existing")
        self.assertIn("ARCHIVED=", output)
        log = next((self.root / "logs").glob("solos_test_*.log")).read_text(encoding="utf-8")
        self.assertIn("cycle complete exit=81", log)

    def test_continuous_mode_returns_to_wait_after_conversion_failure(self):
        self.write_pair()
        (self.root / "OP.dat").write_text("", encoding="utf-8")
        output = self.run_until_waiting_again("--use-existing", "--no-sfis")
        self.assertIn("ARCHIVED=", output)
        self.assertIn("conversion failed", output)

    def test_continuous_mode_returns_to_wait_after_failed_measurements(self):
        self.write_pair(fail=True)
        output = self.run_until_waiting_again("--use-existing", "--no-sfis")
        self.assertIn("FAIL=2,", output)
        self.assertIn("ARCHIVED=", output)

    def test_continuous_archive_failure_defers_unchanged_pair(self):
        xml, pdf = self.write_pair()
        (self.root / "archive").write_text("blocked", encoding="utf-8")
        output = self.run_until_waiting_again(
            "--use-existing", "--no-sfis", waiting_count=3,
        )
        self.assertEqual(output.count("Found pair for SN"), 1)
        self.assertIn("could not archive", output)
        self.assertTrue(xml.exists())
        self.assertTrue(pdf.exists())
        self.assertNotIn(xml.name, wfsp.read_baseline(self.root / "logs/solos_handled.txt"))

    def test_offline_pass_archives_pair_and_csv(self):
        xml, pdf = self.write_pair()
        (self.root / "OP.dat").write_text("  OP123456\n", encoding="utf-8-sig")
        result = self.run_bat("--once", "--use-existing", "--no-sfis")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("FAIL=0,", result.stdout)
        self.assertFalse(xml.exists())
        self.assertFalse(pdf.exists())
        self.assertEqual(len(list((self.root / "archive").glob("*/Lensometer.csv"))), 1)
        with (self.root / "output/UNIT123/Lensometer.csv").open(
            encoding="utf-8-sig", newline=""
        ) as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 17)
        self.assertEqual({row["Operator"] for row in rows}, {"OP123456"})
        self.assertFalse({"Tint_Uniformity", "UVB_Mean"} & {
            row["MeasurementName"] for row in rows
        })
        self.assertIn("PASS=17, FAIL=0, N/A=0", result.stdout)

    def test_dry_run_executes_route_and_upload_without_network(self):
        self.write_pair()
        (self.root / "OP.dat").write_text("OP987654\n", encoding="utf-8")
        result = self.run_bat("--once", "--use-existing", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("GET http://example.invalid/", result.stdout)
        self.assertIn("POST http://example.invalid/", result.stdout)
        self.assertIn('"testStatus": "P"', result.stdout)
        self.assertIn('"operatorId": "OP987654"', result.stdout)
        self.assertNotIn('"Tint_Uniformity"', result.stdout)
        self.assertNotIn('"UVB_Mean"', result.stdout)
        self.assertEqual(result.stdout.count('"measurementStatus"'), 17)

    def test_sfis_failures_archive_sources_without_stale_csv(self):
        self.config["TEST"]["retry"] = "0"
        self.config["TEST"]["sfis_script"] = "sfis_stub.py"
        scenarios = (("/C", 10, 80), ("/C", 11, 81), ("/C", 2, 83),
                     ("/UP", 11, 61), ("/UP", 2, 63))
        for command, sfis_exit, expected in scenarios:
            with self.subTest(command=command, sfis_exit=sfis_exit):
                xml, pdf = self.write_pair()
                (self.root / "sfis_stub.py").write_text(
                    f"import sys\nsys.exit({sfis_exit} if sys.argv[1] == {command!r} else 0)\n",
                    encoding="utf-8",
                )
                output = self.root / "output/UNIT123/Lensometer.csv"
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text("stale CSV", encoding="utf-8")
                before = set((self.root / "archive").glob("*"))
                result = self.run_bat("--once", "--use-existing")
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                self.assertFalse(xml.exists())
                self.assertFalse(pdf.exists())
                created = set((self.root / "archive").glob("*")) - before
                self.assertEqual(len(created), 1)
                destination = created.pop()
                self.assertTrue((destination / xml.name).is_file())
                self.assertTrue((destination / pdf.name).is_file())
                self.assertEqual((destination / "Lensometer.csv").exists(), command == "/UP")

    def test_failed_measurements_are_also_archived(self):
        self.write_pair(fail=True)
        result = self.run_bat("--once", "--use-existing", "--no-sfis")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("FAIL=2,", result.stdout)
        log = next((self.root / "logs").glob("solos_test_*.log")).read_text(encoding="utf-8")
        self.assertIn("result FAIL", log)
        self.assertEqual(len(list((self.root / "archive").glob("*/*.xml"))), 1)

    def test_timeout_is_honored(self):
        result = self.run_bat("--once", "--no-sfis")
        self.assertEqual(result.returncode, 93, result.stdout + result.stderr)

    def test_incomplete_measurement_never_reaches_conversion_or_archive(self):
        xml, pdf = self.write_pair()
        root = ET.parse(xml).getroot()
        spectrum = root.find("Measure/LM/L/TransmittanceData")
        spectrum.remove(spectrum[0])
        ET.ElementTree(root).write(xml, encoding="utf-8")
        result = self.run_bat("--once", "--use-existing", "--no-sfis")
        self.assertEqual(result.returncode, 93, result.stdout + result.stderr)
        self.assertTrue(xml.exists())
        self.assertTrue(pdf.exists())
        self.assertFalse((self.root / "output").exists())
        self.assertFalse((self.root / "archive").exists())
        log = next((self.root / "logs").glob("solos_test_*.log")).read_text(encoding="utf-8")
        self.assertIn("incomplete 315-800 nm spectrum", log)
        self.assertNotIn(xml.name, wfsp.read_baseline(self.root / "logs/solos_handled.txt"))

    def test_invalid_operator_file_archives_sources(self):
        for content in ("", "OP123\nOP456"):
            with self.subTest(content=content):
                xml, pdf = self.write_pair()
                (self.root / "OP.dat").write_text(content, encoding="utf-8")
                result = self.run_bat("--once", "--use-existing", "--no-sfis")
                self.assertEqual(result.returncode, 70, result.stdout + result.stderr)
                self.assertFalse(xml.exists())
                self.assertFalse(pdf.exists())
                self.assertTrue(any((self.root / "archive").glob(f"*/{xml.name}")))

    def test_conversion_failure_archives_sources(self):
        xml, pdf = self.write_pair()
        self.config["TEST"]["converter"] = "tools/wait_for_solos_pair.py"
        result = self.run_bat("--once", "--use-existing", "--no-sfis")
        self.assertEqual(result.returncode, 70, result.stdout + result.stderr)
        self.assertFalse(xml.exists())
        self.assertFalse(pdf.exists())
        self.assertTrue(any((self.root / "archive").glob(f"*/{xml.name}")))
        self.assertFalse(any((self.root / "archive").glob("*/Lensometer.csv")))

    def test_archive_failure_keeps_sources(self):
        xml, pdf = self.write_pair()
        (self.root / "archive").write_text("blocked")
        result = self.run_bat("--once", "--use-existing", "--no-sfis")
        self.assertEqual(result.returncode, 71, result.stdout + result.stderr)
        self.assertTrue(xml.exists())
        self.assertTrue(pdf.exists())
        self.assertNotIn(xml.name, wfsp.read_baseline(self.root / "logs/solos_handled.txt"))

    def test_loop_does_not_reprocess_archived_files(self):
        self.write_pair()
        output = self.run_until_waiting_again("--use-existing", "--no-sfis")
        self.assertEqual(output.count("Found pair for SN"), 1)


if __name__ == "__main__":
    unittest.main()
