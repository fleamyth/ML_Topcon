#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wait_for_solos_pair.py

Watch a folder for a *complete, not-yet-handled* SOLOS output pair:

    <SN>_<YYYYMMDD>_<HHMMSS>_TOPCON_SOLOS_00000.xml
    <SN>_<YYYYMMDD>_<HHMMSS>_Spectacles_Report.pdf

A pair is "ready" only when BOTH files:

  1. exist,
    2. have unchanged size/mtime across observations for ``stable_seconds``,
  3. are complete: the PDF starts with ``%PDF`` and contains ``%%EOF`` in its
    tail, and the XML contains the required measurements and full spectrum,
  4. are not already listed in the baseline file (names already handled). This
     is more robust than timestamps: a file that simply *appears* after the
     watcher starts is picked up regardless of its recorded modification time.

Modes
-----
* default            : block until a ready pair appears (or ``wait_timeout``).
* ``--check-once``   : inspect once, return immediately (for a BAT poll loop).
* ``--print-config`` : print resolved settings as ``KEY=VALUE`` lines.

Output (stdout, ``KEY=VALUE``): SN, XML, PDF, OUT_CSV.

Exit codes: 0 found/ok, 1 not found yet/timeout, 2 config or input error.
"""

from __future__ import annotations

import argparse
import configparser
from datetime import datetime
import math
import json
import re
import shutil
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

EXIT_FOUND = 0
EXIT_NOT_YET = 1
EXIT_CONFIG = 2

XML_RE = re.compile(
    r"^(?P<sn>.+?)_(?P<date>\d{8})_(?P<time>\d{6})_TOPCON_SOLOS_00000\.xml$",
    re.IGNORECASE,
)

TEST_DEFAULTS = {
    "watch_dir": "",
    "xml_pattern": "*_TOPCON_SOLOS_00000.xml",
    "pdf_suffix": "_Spectacles_Report.pdf",
    "poll_seconds": "2",
    "stable_seconds": "5",
    "wait_timeout": "600",
    "use_existing": "0",
    "output_dir": "output",
    "archive_dir": "archive",
    "converter": "solos_xml_to_pcbafct.py",
    "sfis_script": r"RESTSFIS-diag\RESTSFIS-diag.py",
    "sfis_ini": r"RESTSFIS-diag\RESTSFIS-diag.ini",
    "operator": "",
    "dry_run": "0",
    "retry": "2",
    "once": "0",
}

SCREEN_DEFAULTS = {
    "enabled": "1",
    "screen_dir": "",
    "screen_exe": "Screen-diag.exe",
    "uilogresult_exe": "UILogResult-auto.exe",
    "wait_message": "waiting for (xml) files...",
    "pass_message": "PASS",
}

OUTPUT_NAME = "Lensometer.csv"


def archive_pair(xml: Path, pdf: Path, archive_dir: Path,
                 csv: Path | None = None) -> Path:
    archive_dir.mkdir(parents=True, exist_ok=True)
    destination = Path(tempfile.mkdtemp(prefix=f"{xml.stem}_", dir=archive_dir))
    try:
        for source in (xml, pdf) if csv is None else (xml, pdf, csv):
            shutil.copy2(source, destination / source.name)
    except OSError:
        shutil.rmtree(destination)
        raise
    xml.unlink()
    pdf.unlink()
    return destination


def _as_bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _as_float(value: str, default: float) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def load_config(config_path: Path) -> tuple[dict[str, str], dict[str, str]]:
    parser = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    if config_path.is_file():
        try:
            with config_path.open("r", encoding="utf-8-sig") as handle:
                parser.read_file(handle)
        except (OSError, configparser.Error) as error:
            raise SystemExit(f"ERROR: cannot read config {config_path}: {error}")

    test = dict(TEST_DEFAULTS)
    screen = dict(SCREEN_DEFAULTS)
    if parser.has_section("TEST"):
        test.update({k.lower(): v.strip() for k, v in parser.items("TEST")})
    if parser.has_section("SCREEN"):
        screen.update({k.lower(): v.strip() for k, v in parser.items("SCREEN")})
    return test, screen


def resolve(base: Path, value: str) -> Path:
    value = (value or "").strip().strip('"')
    path = Path(value)
    return path if path.is_absolute() else (base / path)


def read_baseline(path: Path | None) -> set[str]:
    """Return the set of file names already handled (one per line)."""
    if not path or not path.is_file():
        return set()
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return {line.strip() for line in handle if line.strip()}
    except OSError:
        return set()


def pair_signature(xml: Path, pdf: Path) -> list[list[int] | None]:
    signature = []
    for path in (xml, pdf):
        try:
            status = path.stat()
            signature.append([status.st_size, status.st_mtime_ns, status.st_ino])
        except OSError:
            signature.append(None)
    return signature


def read_deferred(path: Path | None) -> dict:
    if path is None or not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        state = json.load(handle)
    if not isinstance(state, dict):
        raise ValueError("invalid deferred state")
    return state


def pdf_is_complete(path: Path) -> bool:
    try:
        size = path.stat().st_size
        if size < 8:
            return False
        with path.open("rb") as handle:
            if not handle.read(5).startswith(b"%PDF"):
                return False
            handle.seek(max(0, size - 2048))
            tail = handle.read()
        return b"%%EOF" in tail
    except OSError:
        return False


def xml_validation_error(path: Path) -> str | None:
    try:
        root = ET.parse(str(path)).getroot()
    except (ET.ParseError, OSError) as error:
        return f"XML not readable/well-formed: {error}"
    for element in root.iter():
        element.tag = element.tag.rsplit("}", 1)[-1]
    for name in ("Patient/ID", "Date", "Time", "ModelName", "MachineNo"):
        if not (root.findtext(f"Common/{name}") or "").strip():
            return f"missing Common/{name}"
    try:
        datetime.strptime(
            f"{root.findtext('Common/Date')} {root.findtext('Common/Time')}",
            "%Y-%m-%d %H:%M:%S",
        )
    except ValueError:
        return "invalid measurement date/time"

    def finite_number(text: str | None) -> bool:
        try:
            return math.isfinite(float(text or ""))
        except ValueError:
            return False

    for eye_name in ("R", "L"):
        eye = root.find(f"Measure/LM/{eye_name}")
        if eye is None:
            return f"missing {eye_name} eye measurements"
        for name in ("Sphere", "Cylinder", "UVTransmittance", "VisibleTransmittance"):
            if not finite_number(eye.findtext(name)):
                return f"{eye_name}: missing/invalid {name}"
        for name in ("PrismX", "PrismY"):
            element = eye.find(name)
            if element is None:
                return f"{eye_name}: missing {name}"
            if (element.text or "").strip() and not finite_number(element.text):
                return f"{eye_name}: invalid {name}"
        wavelengths = set()
        for point in eye.findall("TransmittanceData/TransmittanceValue"):
            wavelength = point.findtext("WaveLength")
            if not finite_number(wavelength) or not finite_number(point.findtext("Transmittance")):
                return f"{eye_name}: invalid spectrum point"
            value = float(wavelength or "")
            if not value.is_integer() or value in wavelengths:
                return f"{eye_name}: invalid/duplicate wavelength {value}"
            wavelengths.add(value)
        if not set(range(315, 801)).issubset(wavelengths):
            return f"{eye_name}: incomplete 315-800 nm spectrum"
    return None


def xml_is_complete(path: Path) -> bool:
    return xml_validation_error(path) is None


def read_patient_id(path: Path) -> str:
    try:
        root = ET.parse(str(path)).getroot()
    except (ET.ParseError, OSError):
        return ""
    for elem in root.iter():
        if elem.tag.rsplit("}", 1)[-1] == "Patient":
            for child in elem:
                if child.tag.rsplit("}", 1)[-1] == "ID":
                    return (child.text or "").strip()
    return ""


def is_stable(path: Path, stable_seconds: float, now: float) -> bool:
    try:
        return (now - path.stat().st_mtime) >= stable_seconds
    except OSError:
        return False


class FileStability:
    def __init__(self, seconds: float):
        self.seconds = max(0.0, seconds)
        self.observations: dict[Path, tuple[tuple[int, int, int], float]] = {}

    def observe(self, path: Path) -> bool:
        try:
            status = path.stat()
        except OSError:
            self.observations.pop(path, None)
            return False
        signature = (status.st_size, status.st_mtime_ns, status.st_ino)
        now = time.monotonic()
        previous = self.observations.get(path)
        if previous is None or previous[0] != signature:
            self.observations[path] = (signature, now)
        return now - self.observations[path][1] >= self.seconds


def _candidates(watch_dir: Path, xml_pattern: str) -> list[Path]:
    if not watch_dir.is_dir():
        return []
    try:
        return sorted(watch_dir.glob(xml_pattern), key=lambda path: path.stat().st_mtime,
                      reverse=True)
    except OSError:
        return []


def _pdf_for(xml: Path, match: re.Match[str], pdf_suffix: str) -> Path:
    return xml.with_name(
        f"{match.group('sn')}_{match.group('date')}_{match.group('time')}{pdf_suffix}"
    )


def find_ready_pair(
    watch_dir: Path,
    xml_pattern: str,
    pdf_suffix: str,
    stable_seconds: float,
    excluded: set[str],
    use_existing: bool,
    stability: FileStability | None = None,
    deferred: dict | None = None,
) -> tuple[Path, Path, str] | None:
    tracker = stability if stability is not None else FileStability(stable_seconds)
    for xml in _candidates(watch_dir, xml_pattern):
        if not use_existing and xml.name in excluded:
            continue
        match = XML_RE.match(xml.name)
        if not match:
            continue
        pdf = _pdf_for(xml, match, pdf_suffix)
        if deferred and deferred.get(str(xml.resolve())) == pair_signature(xml, pdf):
            continue
        xml_stable = tracker.observe(xml)
        pdf_stable = tracker.observe(pdf)
        if not xml_stable or not pdf_stable:
            continue
        if not xml_is_complete(xml) or not pdf_is_complete(pdf):
            continue
        sn = read_patient_id(xml)
        unchanged = tracker.observe(xml) and tracker.observe(pdf)
        if unchanged and sn:
            return xml, pdf, sn
    return None


def diagnose(
    watch_dir: Path,
    xml_pattern: str,
    pdf_suffix: str,
    stable_seconds: float,
    excluded: set[str],
    use_existing: bool,
) -> str | None:
    """Explain (for the log) why the newest matching XML is not ready yet."""
    candidates = _candidates(watch_dir, xml_pattern)
    if not candidates:
        return None
    xml = candidates[0]
    if not use_existing and xml.name in excluded:
        return None
    match = XML_RE.match(xml.name)
    if not match:
        return f"{xml.name}: name does not match <SN>_<date>_<time>_TOPCON_SOLOS_00000.xml"
    pdf = _pdf_for(xml, match, pdf_suffix)
    if not pdf.is_file():
        return f"{xml.name}: waiting for {pdf.name}"
    if not is_stable(xml, stable_seconds, time.time()) or not is_stable(pdf, stable_seconds, time.time()):
        return f"{xml.name}: waiting for files to settle"
    error = xml_validation_error(xml)
    if error:
        return f"{xml.name}: {error}"
    if not pdf_is_complete(pdf):
        return f"{pdf.name}: PDF incomplete (no %%EOF)"
    return f"{xml.name}: observing unchanged file size/mtime"


def emit_settings(test: dict[str, str], screen: dict[str, str], base: Path) -> None:
    watch_dir = resolve(base, test["watch_dir"] or str(base))
    screen_dir = resolve(base, screen["screen_dir"]) if screen["screen_dir"].strip() else base
    print(f"WATCH_DIR={watch_dir}")
    print(f"XML_PATTERN={test['xml_pattern']}")
    print(f"PDF_SUFFIX={test['pdf_suffix']}")
    print(f"POLL_SECONDS={test['poll_seconds']}")
    print(f"STABLE_SECONDS={test['stable_seconds']}")
    print(f"WAIT_TIMEOUT={test['wait_timeout']}")
    print(f"USE_EXISTING={test['use_existing']}")
    print(f"OUTPUT_DIR={resolve(base, test['output_dir'])}")
    print(f"ARCHIVE_DIR={resolve(base, test['archive_dir'])}")
    print(f"CONVERTER={resolve(base, test['converter'])}")
    print(f"SFIS_SCRIPT={resolve(base, test['sfis_script'])}")
    print(f"SFIS_INI={resolve(base, test['sfis_ini'])}")
    print(f"OPERATOR={test['operator']}")
    print(f"DRY_RUN={test['dry_run']}")
    print(f"RETRY={test['retry']}")
    print(f"ONCE={test['once']}")
    print(f"SCREEN_ENABLED={screen['enabled']}")
    print(f"SCREEN_EXE={screen_dir / screen['screen_exe']}")
    print(f"UILOGRESULT_EXE={screen_dir / screen['uilogresult_exe']}")
    print(f"WAIT_MESSAGE={screen['wait_message']}")
    print(f"PASS_MESSAGE={screen['pass_message']}")


def emit_result(xml: Path, pdf: Path, sn: str, out_dir: Path) -> None:
    print(f"SN={sn}")
    print(f"XML={xml}")
    print(f"PDF={pdf}")
    print(f"OUT_CSV={out_dir / sn / OUTPUT_NAME}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Wait for a complete SOLOS XML+PDF pair.")
    parser.add_argument("--config", default=None, help="Path to solos_test.ini")
    parser.add_argument("--print-config", action="store_true")
    parser.add_argument("--archive-pair", nargs=2, metavar=("XML", "PDF"))
    parser.add_argument("--csv", default=None, help="CSV to preserve with the archived pair")
    parser.add_argument("--deferred", default=None, help="Temporary failed-pair state")
    parser.add_argument("--defer-pair", nargs=2, metavar=("XML", "PDF"))
    parser.add_argument("--check-once", action="store_true")
    parser.add_argument("--dir", default=None, help="Override watch_dir")
    parser.add_argument("--baseline", default=None,
                        help="File listing XML names already handled")
    parser.add_argument("--snapshot-baseline", action="store_true",
                        help="Write the current matching XML names to --baseline and exit")
    parser.add_argument("--use-existing", action="store_true")
    parser.add_argument("--poll", type=float, default=None)
    parser.add_argument("--stable", type=float, default=None)
    parser.add_argument("--timeout", type=float, default=None)
    args = parser.parse_args(argv)

    base = Path(args.config).resolve().parent if args.config else Path.cwd()
    config_path = (Path(args.config) if args.config
                   else Path(__file__).resolve().with_name("solos_test.ini"))
    test, screen = load_config(config_path)

    try:
        deferred_path = Path(args.deferred) if args.deferred else None
        deferred = read_deferred(deferred_path)
        if args.defer_pair:
            if deferred_path is None:
                raise ValueError("--defer-pair requires --deferred")
            xml, pdf = (Path(value) for value in args.defer_pair)
            deferred[str(xml.resolve())] = pair_signature(xml, pdf)
            deferred_path.write_text(json.dumps(deferred), encoding="utf-8")
            return EXIT_FOUND
    except (OSError, ValueError) as error:
        print(f"ERROR: cannot read/write deferred pair state: {error}", file=sys.stderr)
        return EXIT_CONFIG

    if args.archive_pair:
        try:
            destination = archive_pair(
                *(Path(value) for value in args.archive_pair),
                resolve(base, test["archive_dir"]),
                Path(args.csv) if args.csv else None,
            )
        except OSError as error:
            print(f"ERROR: cannot archive pair: {error}", file=sys.stderr)
            return EXIT_CONFIG
        print(f"ARCHIVED={destination}")
        return EXIT_FOUND

    watch_dir = Path(args.dir) if args.dir else resolve(base, test["watch_dir"] or str(base))
    out_dir = resolve(base, test["output_dir"])
    poll = args.poll if args.poll is not None else _as_float(test["poll_seconds"], 2.0)
    stable = args.stable if args.stable is not None else _as_float(test["stable_seconds"], 5.0)
    timeout = args.timeout if args.timeout is not None else _as_float(test["wait_timeout"], 600.0)
    use_existing = args.use_existing or _as_bool(test["use_existing"])
    excluded = read_baseline(Path(args.baseline)) if args.baseline else set()

    if args.print_config:
        emit_settings(test, screen, base)
        return EXIT_FOUND

    if args.snapshot_baseline:
        if not args.baseline:
            print("ERROR: --snapshot-baseline requires --baseline", file=sys.stderr)
            return EXIT_CONFIG
        names = [p.name for p in _candidates(watch_dir, test["xml_pattern"])]
        Path(args.baseline).write_text(
            "".join(f"{name}\n" for name in names), encoding="utf-8"
        )
        return EXIT_FOUND

    stability = FileStability(stable)

    def attempt() -> tuple[Path, Path, str] | None:
        return find_ready_pair(watch_dir, test["xml_pattern"], test["pdf_suffix"],
                               stable, excluded, use_existing, stability, deferred)

    if args.check_once:
        found = attempt()
        if found:
            emit_result(*found, out_dir)
            return EXIT_FOUND
        reason = diagnose(watch_dir, test["xml_pattern"], test["pdf_suffix"],
                          stable, excluded, use_existing)
        if reason:
            print(f"waiting: {reason}", file=sys.stderr)
        return EXIT_NOT_YET

    deadline = time.monotonic() + timeout
    last_reason = None
    while True:
        found = attempt()
        if found:
            emit_result(*found, out_dir)
            return EXIT_FOUND
        reason = diagnose(watch_dir, test["xml_pattern"], test["pdf_suffix"],
                          stable, excluded, use_existing)
        if reason and reason != last_reason:
            print(f"waiting: {reason}", file=sys.stderr)
        last_reason = reason
        if time.monotonic() >= deadline:
            print(f"ERROR: timed out after {timeout:g}s waiting in {watch_dir}", file=sys.stderr)
            return EXIT_NOT_YET
        time.sleep(max(0.1, poll))


if __name__ == "__main__":
    raise SystemExit(main())
