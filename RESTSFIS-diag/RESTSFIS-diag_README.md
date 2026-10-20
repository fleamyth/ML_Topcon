# SFIS REST CLI

`RESTSFIS-diag.py` replaces the KINGSFIS `/C` and `/UP` SOAP operations with the
REST APIs in `SFIS Spec_RevisionG_20260722.xlsx`. It uses only the Python
standard library.

Edit `RESTSFIS-diag.ini` beside the script before use:

```ini
[SFIS]
base_url = http://testrestapi-szmldb0-n0.sz.pegatroncorp.com/mesinterface/v1
process = DAAT
equipment =
timeout = 30
```

The specification currently lists a test endpoint. Obtain the production URL,
Process Name, and Equipment Name from the SFIS team before release. Equipment
defaults to the local hostname when it is blank. Command-line values override
environment variables, which override the INI values and hostname default.

## Check route (`/C`)

```bat
python RESTSFIS-diag.py /C -sn PXXXXXXXXXXX
```

Use `--dry-run` to print the request without sending it. A different config can
be selected after the command with `--config`:

```bat
python RESTSFIS-diag.py /C --config D:\FCT_TestSuite\SFIS-test.ini ^
  -sn PXXXXXXXXXXX --dry-run
```

## Upload KING CSV (`/UP`)

```bat
python RESTSFIS-diag.py /UP -log result.csv
```

The command reads `serialNumber`, `process`, operator, start/stop times, and
measurements from the KING CSV. `assemblyNumber` defaults to the serial number.
Values can be overridden when necessary:

```bat
python RESTSFIS-diag.py /UP -log result.csv --equipment MLDW2165 ^
  --process DAAT -sn PXXXXXXXXXXX -op S12345678 --assembly-number PXXXXXXXXXXX
```

Always inspect the payload before the first upload from a new log format:

```bat
python RESTSFIS-diag.py /UP -log result.csv --dry-run
```

Exit codes: `0` success, `2` invalid CLI arguments, `10` route rejected
(HTTP 442), `11` HTTP/server failure, and `12` input or connection failure.