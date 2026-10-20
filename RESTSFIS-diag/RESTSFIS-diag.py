#!/usr/bin/env python3
"""REST replacement for the KINGSFIS /C and /UP commands."""

from __future__ import annotations

import argparse
import configparser
import csv
from datetime import datetime
import json
import os
from pathlib import Path
import socket
import sys
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


EXIT_OK = 0
EXIT_USAGE = 2
EXIT_ROUTE_REJECTED = 10
EXIT_HTTP_ERROR = 11
EXIT_INPUT_ERROR = 12

DATETIME_FORMATS = (
    "%m/%d/%Y %I:%M:%S %p",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
)


class SfisError(Exception):
    """An expected input or SFIS communication failure."""


def default_config_path() -> Path:
    return Path(__file__).resolve().with_suffix(".ini")


def config_path_from_args(argv: list[str]) -> tuple[Path, bool]:
    for index, argument in enumerate(argv):
        if argument == "--config":
            if index + 1 >= len(argv):
                raise SfisError("--config requires a file path")
            return Path(argv[index + 1]), True
        if argument.startswith("--config="):
            return Path(argument.split("=", 1)[1]), True
    return default_config_path(), False


def load_config(config_path: Path, required: bool = False) -> dict[str, str]:
    if not config_path.is_file():
        if required:
            raise SfisError(f"Config file does not exist: {config_path}")
        return {}

    parser = configparser.ConfigParser()
    try:
        with config_path.open("r", encoding="utf-8-sig") as config_file:
            parser.read_file(config_file)
    except (OSError, configparser.Error) as error:
        raise SfisError(f"Cannot read config file {config_path}: {error}") from error

    if not parser.has_section("SFIS"):
        raise SfisError(f"Config file has no [SFIS] section: {config_path}")
    return {key: value.strip() for key, value in parser.items("SFIS")}


def config_float(config: dict[str, str], key: str, default: float) -> float:
    value = config.get(key, "")
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        raise SfisError(f"Config value {key} must be a number: {value}") from None


def configured_value(config: dict[str, str], key: str, environment: str) -> str:
    return os.environ.get(environment, config.get(key, ""))


def configured_equipment(config: dict[str, str]) -> str:
    return configured_value(config, "equipment", "SFIS_EQUIPMENT_NAME") or socket.gethostname()


def optional(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def parse_datetime(value: str) -> datetime:
    value = value.strip()
    if not value:
        raise SfisError("The log contains an empty date/time value")

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        for date_format in DATETIME_FORMATS:
            try:
                parsed = datetime.strptime(value, date_format)
                break
            except ValueError:
                continue
        else:
            raise SfisError(f"Unsupported date/time format: {value}") from None

    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def isoformat(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds")


def endpoint(base_url: str, path: str) -> str:
    base_url = base_url.strip().rstrip("/")
    if not base_url:
        raise SfisError("SFIS base URL is required; use --base-url or SFIS_BASE_URL")
    return f"{base_url}/{path.lstrip('/')}"


def decode_response(data: bytes, content_type: str | None) -> Any:
    text = data.decode("utf-8", errors="replace").strip()
    if "json" in (content_type or "").lower() or text.startswith(("{", "[", '"')):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    return text


def request_json(
    method: str,
    url: str,
    timeout: float,
    payload: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"

    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, decode_response(
                response.read(), response.headers.get("Content-Type")
            )
    except HTTPError as error:
        response_body = decode_response(error.read(), error.headers.get("Content-Type"))
        return error.code, response_body
    except URLError as error:
        raise SfisError(f"Cannot connect to SFIS: {error.reason}") from error
    except TimeoutError as error:
        raise SfisError(f"SFIS request timed out after {timeout:g} seconds") from error


def normalized_row(row: dict[str, str | None]) -> dict[str, str]:
    return {
        key.strip().lower(): (value or "").strip()
        for key, value in row.items()
        if key is not None
    }


def get_field(row: dict[str, str], *names: str) -> str:
    for name in names:
        value = row.get(name.lower(), "")
        if value:
            return value
    return ""


def is_measurement_pass(row: dict[str, str]) -> bool:
    error_code = get_field(row, "ErrorCode", "PegaCode")
    return error_code.upper() in {"", "0", "N/A", "NA", "PASS", "P"}


def read_king_csv(log_path: Path) -> list[dict[str, str]]:
    if not log_path.is_file():
        raise SfisError(f"Log file does not exist: {log_path}")

    last_error: UnicodeDecodeError | None = None
    for encoding in ("utf-8-sig", "mbcs"):
        try:
            with log_path.open("r", encoding=encoding, newline="") as log_file:
                reader = csv.DictReader(log_file)
                if reader.fieldnames is None:
                    raise SfisError(f"CSV has no header: {log_path}")
                field_names = {name.strip().lower() for name in reader.fieldnames}
                required = {"serialnumber", "startdatetime", "enddatetime"}
                missing = sorted(required - field_names)
                if missing:
                    raise SfisError(
                        "CSV is missing required column(s): " + ", ".join(missing)
                    )
                rows = [normalized_row(row) for row in reader if any(row.values())]
                if not rows:
                    raise SfisError(f"CSV contains no result rows: {log_path}")
                return rows
        except UnicodeDecodeError as error:
            last_error = error

    raise SfisError(f"Cannot decode CSV file: {last_error}")


def one_value(rows: Iterable[dict[str, str]], *names: str) -> str:
    values = {get_field(row, *names) for row in rows}
    values.discard("")
    if not values:
        return ""
    if len(values) != 1:
        raise SfisError(f"Log contains multiple values for {names[0]}: {sorted(values)}")
    return values.pop()


def build_logging_payload(args: argparse.Namespace) -> dict[str, Any]:
    rows = read_king_csv(Path(args.log))
    serial_number = args.sn or one_value(rows, "SerialNumber")
    if not serial_number:
        raise SfisError("serialNumber is required; use -sn or provide it in the CSV")

    process = args.process or one_value(rows, "Operation")
    if not process:
        raise SfisError("process is required; use --process or provide Operation in the CSV")

    equipment_name = args.equipment or os.environ.get("SFIS_EQUIPMENT_NAME")
    if not equipment_name:
        raise SfisError(
            "equipmentName is required; use --equipment or SFIS_EQUIPMENT_NAME"
        )

    start_times = [parse_datetime(get_field(row, "StartDateTime")) for row in rows]
    stop_times = [parse_datetime(get_field(row, "EndDateTime", "StopDateTime")) for row in rows]
    all_passed = all(is_measurement_pass(row) for row in rows)
    test_status = args.status or ("P" if all_passed else "F")

    measurements = []
    for row in rows:
        passed = is_measurement_pass(row)
        name = get_field(row, "MeasurementName", "TestName") or "Unnamed test"
        value = get_field(row, "Value")
        measurements.append(
            {
                "name": name,
                "value": value,
                "lowerLimit": optional(get_field(row, "LowerLimit")),
                "upperLimit": optional(get_field(row, "UpperLimit")),
                "units": optional(get_field(row, "Units")),
                "measurementStatus": passed,
                "nominalValue": optional(get_field(row, "NominalValue")),
                "message": optional(get_field(row, "ErrorDescription")),
            }
        )

    failed_rows = [row for row in rows if not is_measurement_pass(row)]
    fail_label = args.fail_label
    fail_message = args.fail_message
    if failed_rows:
        fail_label = fail_label or get_field(
            failed_rows[0], "MeasurementName", "TestName", "ErrorCode"
        )
        fail_message = fail_message or get_field(
            failed_rows[0], "ErrorDescription", "PegaCode", "ErrorCode"
        )

    return {
        "serialNumber": serial_number,
        "process": process,
        "equipmentName": equipment_name,
        "assemblyNumber": args.assembly_number or serial_number,
        "assemblyRevision": optional(args.assembly_revision),
        "assemblyVersion": optional(args.assembly_version),
        "testStatus": test_status,
        "startDateTime": isoformat(min(start_times)),
        "stopDateTime": isoformat(max(stop_times)),
        "operatorId": optional(args.op or one_value(rows, "Operator")),
        "line": optional(args.line),
        "failLabel": optional(fail_label),
        "failMessage": optional(fail_message),
        "fixture": optional(args.fixture),
        "measurements": measurements,
    }


def print_response(status: int, body: Any) -> None:
    print(f"HTTP {status}")
    if isinstance(body, (dict, list)):
        print(json.dumps(body, ensure_ascii=False, indent=2))
    else:
        print(body)


def check_route(args: argparse.Namespace) -> int:
    if not args.process:
        raise SfisError("process is required; set it in config or use --process")
    if not args.equipment:
        raise SfisError("equipmentName is required; set it in config or use --equipment")
    query = urlencode(
        {
            "process": args.process,
            "equipmentName": args.equipment,
            "serialNumber": args.sn,
        }
    )
    url = endpoint(args.base_url, "api/routing/routecheck") + "?" + query
    print(f"GET {url}")
    if args.dry_run:
        return EXIT_OK

    status, body = request_json("GET", url, args.timeout)
    print_response(status, body)
    if status == 200 and str(body).upper() == "PASS":
        return EXIT_OK
    if status == 442:
        return EXIT_ROUTE_REJECTED
    return EXIT_HTTP_ERROR


def upload_log(args: argparse.Namespace) -> int:
    payload = build_logging_payload(args)
    url = endpoint(args.base_url, "api/logging")
    print(f"POST {url}")
    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return EXIT_OK

    status, body = request_json("POST", url, args.timeout, payload)
    print_response(status, body)
    return EXIT_OK if status == 200 else EXIT_HTTP_ERROR


def add_connection_arguments(
    parser: argparse.ArgumentParser,
    config: dict[str, str],
    config_path: Path,
) -> None:
    parser.add_argument(
        "--config",
        default=str(config_path),
        help="INI config path (default: RESTSFIS-diag.ini beside the script)",
    )
    parser.add_argument(
        "--base-url",
        default=configured_value(config, "base_url", "SFIS_BASE_URL"),
        help="Base URL ending in /mesinterface/v1",
    )
    parser.add_argument(
        "--timeout", type=float, default=config_float(config, "timeout", 30.0)
    )
    parser.add_argument("--dry-run", action="store_true")


def build_parser(
    config: dict[str, str] | None = None,
    config_path: Path | None = None,
) -> argparse.ArgumentParser:
    config = config or {}
    config_path = config_path or default_config_path()
    parser = argparse.ArgumentParser(
        description="REST replacement for KINGSFIS /C and /UP",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    route_parser = commands.add_parser("/C", aliases=["/c", "c", "C"])
    add_connection_arguments(route_parser, config, config_path)
    route_parser.add_argument("-sn", required=True, help="Unit serial number")
    route_parser.add_argument(
        "--process",
        default=configured_value(config, "process", "SFIS_PROCESS"),
        help="MES process name",
    )
    route_parser.add_argument(
        "--equipment",
        default=configured_equipment(config),
        help="MES equipment name (default: local hostname)",
    )
    route_parser.set_defaults(handler=check_route)

    upload_parser = commands.add_parser("/UP", aliases=["/up", "up", "UP"])
    add_connection_arguments(upload_parser, config, config_path)
    upload_parser.add_argument("-log", required=True, help="KING result CSV")
    upload_parser.add_argument("-sn", help="Override SerialNumber from CSV")
    upload_parser.add_argument("-op", help="Override Operator from CSV")
    upload_parser.add_argument(
        "--process",
        default=configured_value(config, "process", "SFIS_PROCESS"),
        help="Override Operation from CSV",
    )
    upload_parser.add_argument(
        "--equipment",
        default=configured_equipment(config),
        help="MES equipment name (default: local hostname)",
    )
    upload_parser.add_argument("--assembly-number", default=config.get("assembly_number"))
    upload_parser.add_argument(
        "--assembly-revision", default=config.get("assembly_revision")
    )
    upload_parser.add_argument(
        "--assembly-version", default=config.get("assembly_version")
    )
    upload_parser.add_argument("--status", choices=("P", "F", "A"))
    upload_parser.add_argument("--line", default=config.get("line"))
    upload_parser.add_argument("--fixture", default=config.get("fixture"))
    upload_parser.add_argument("--fail-label")
    upload_parser.add_argument("--fail-message")
    upload_parser.set_defaults(handler=upload_log)
    return parser


def main(argv: list[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    try:
        config_path, config_required = config_path_from_args(raw_args)
        config = load_config(config_path, config_required)
        parser = build_parser(config, config_path)
        args = parser.parse_args(raw_args)
        return args.handler(args)
    except SfisError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return EXIT_INPUT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())