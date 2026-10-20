import argparse
from contextlib import redirect_stdout
import csv
import io
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "RESTSFIS-diag.py"
MODULE_SPEC = importlib.util.spec_from_file_location("restsfis_diag", MODULE_PATH)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
restsfis_diag = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(restsfis_diag)


FIELD_NAMES = [
    "TSRID",
    "SerialNumber",
    "PartNumber",
    "Operation",
    "TesterName",
    "TestName",
    "TestVariation",
    "ErrorCode",
    "Operator",
    "StartDateTime",
    "EndDateTime",
    "ProductionTest",
    "TestSuiteName",
    "ErrorDescription",
    "MeasurementName",
    "Value",
    "Units",
    "LowerLimit",
    "UpperLimit",
    "PegaCode",
    "TicketID",
]


class LoggingPayloadTests(unittest.TestCase):
    def write_log(self, rows: list[dict[str, str]]) -> Path:
        temporary_file = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", suffix=".csv", delete=False
        )
        with temporary_file:
            writer = csv.DictWriter(temporary_file, fieldnames=FIELD_NAMES)
            writer.writeheader()
            writer.writerows(rows)
        self.addCleanup(Path(temporary_file.name).unlink)
        return Path(temporary_file.name)

    def arguments(self, log_path: Path) -> argparse.Namespace:
        return argparse.Namespace(
            log=str(log_path),
            sn=None,
            op=None,
            process=None,
            equipment="MLDW2165",
            assembly_number=None,
            assembly_revision=None,
            assembly_version=None,
            status=None,
            line=None,
            fixture=None,
            fail_label=None,
            fail_message=None,
        )

    def test_builds_pass_payload_from_king_csv(self) -> None:
        log_path = self.write_log(
            [
                {
                    "SerialNumber": "P123",
                    "Operation": "DAAT",
                    "TestName": "Voltage test",
                    "ErrorCode": "0",
                    "Operator": "S123",
                    "StartDateTime": "07/23/2026 10:00:00 AM",
                    "EndDateTime": "07/23/2026 10:01:00 AM",
                    "MeasurementName": "Voltage",
                    "Value": "5.0",
                    "Units": "V",
                    "LowerLimit": "4.8",
                    "UpperLimit": "5.2",
                }
            ]
        )

        payload = restsfis_diag.build_logging_payload(self.arguments(log_path))

        self.assertEqual(payload["serialNumber"], "P123")
        self.assertEqual(payload["assemblyNumber"], "P123")
        self.assertEqual(payload["process"], "DAAT")
        self.assertEqual(payload["testStatus"], "P")
        self.assertTrue(payload["measurements"][0]["measurementStatus"])
        self.assertEqual(payload["measurements"][0]["upperLimit"], "5.2")

    def test_failure_sets_status_and_message(self) -> None:
        log_path = self.write_log(
            [
                {
                    "SerialNumber": "P123",
                    "Operation": "DAAT",
                    "TestName": "Camera test",
                    "ErrorCode": "8F01",
                    "Operator": "S123",
                    "StartDateTime": "2026-07-23 10:00:00",
                    "EndDateTime": "2026-07-23 10:01:00",
                    "ErrorDescription": "Camera not found",
                }
            ]
        )

        payload = restsfis_diag.build_logging_payload(self.arguments(log_path))

        self.assertEqual(payload["testStatus"], "F")
        self.assertEqual(payload["failLabel"], "Camera test")
        self.assertEqual(payload["failMessage"], "Camera not found")
        self.assertFalse(payload["measurements"][0]["measurementStatus"])

    def test_rejects_multiple_serial_numbers(self) -> None:
        rows = []
        for serial_number in ("P123", "P456"):
            rows.append(
                {
                    "SerialNumber": serial_number,
                    "Operation": "DAAT",
                    "ErrorCode": "0",
                    "StartDateTime": "2026-07-23 10:00:00",
                    "EndDateTime": "2026-07-23 10:01:00",
                }
            )
        log_path = self.write_log(rows)

        with self.assertRaisesRegex(restsfis_diag.SfisError, "multiple values"):
            restsfis_diag.build_logging_payload(self.arguments(log_path))


class CommandLineTests(unittest.TestCase):
    def test_defaults_equipment_to_local_hostname(self) -> None:
        with patch.object(restsfis_diag.socket, "gethostname", return_value="PRCAL-PRE01"):
            parser = restsfis_diag.build_parser()
            args = parser.parse_args(["/c", "-sn", "P123", "--process", "DAAT"])

        self.assertEqual(args.equipment, "PRCAL-PRE01")

    def test_route_check_displays_complete_url(self) -> None:
        args = argparse.Namespace(
            process="DAAT Test",
            equipment="MLDW2165",
            sn="P123/456",
            base_url="http://sfis.test/mesinterface/v1",
            dry_run=False,
            timeout=30.0,
        )
        output = io.StringIO()

        with patch.object(restsfis_diag, "request_json", return_value=(200, "PASS")):
            with redirect_stdout(output):
                result = restsfis_diag.check_route(args)

        self.assertEqual(result, restsfis_diag.EXIT_OK)
        self.assertIn(
            "GET http://sfis.test/mesinterface/v1/api/routing/routecheck"
            "?process=DAAT+Test&equipmentName=MLDW2165&serialNumber=P123%2F456",
            output.getvalue(),
        )

    def test_accepts_legacy_lowercase_slash_commands(self) -> None:
        parser = restsfis_diag.build_parser()

        route_args = parser.parse_args(
            [
                "/c",
                "-sn",
                "P123",
                "--process",
                "DAAT",
                "--equipment",
                "MLDW2165",
            ]
        )
        upload_args = parser.parse_args(
            ["/up", "-log", "result.csv", "--equipment", "MLDW2165"]
        )

        self.assertIs(route_args.handler, restsfis_diag.check_route)
        self.assertIs(upload_args.handler, restsfis_diag.upload_log)

    def test_loads_config_defaults_and_allows_cli_override(self) -> None:
        temporary_file = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".ini", delete=False
        )
        with temporary_file:
            temporary_file.write(
                "[SFIS]\n"
                "base_url = http://config.test/mesinterface/v1\n"
                "process = CONFIG_PROCESS\n"
                "equipment = CONFIG_EQUIPMENT\n"
                "timeout = 45\n"
            )
        config_path = Path(temporary_file.name)
        self.addCleanup(config_path.unlink)

        config = restsfis_diag.load_config(config_path, required=True)
        parser = restsfis_diag.build_parser(config, config_path)
        config_args = parser.parse_args(["/c", "-sn", "P123"])
        override_args = parser.parse_args(
            ["/c", "-sn", "P123", "--process", "CLI_PROCESS"]
        )

        self.assertEqual(config_args.base_url, "http://config.test/mesinterface/v1")
        self.assertEqual(config_args.process, "CONFIG_PROCESS")
        self.assertEqual(config_args.equipment, "CONFIG_EQUIPMENT")
        self.assertEqual(config_args.timeout, 45.0)
        self.assertEqual(override_args.process, "CLI_PROCESS")


if __name__ == "__main__":
    unittest.main()