# Counsel Collector

Counsel Collector is a compact PyQt/Selenium helper for collecting counsel information from Lexis CourtLink docket results.

## What It Does

1. Logs into Lexis Plus.
2. Opens the configured CourtLink search page.
3. Selects one or more supported circuit courts.
4. Searches one or more docket numbers for each selected court.
5. Opens each result.
6. Extracts Header and Participants/counsel information.
7. Saves Word-friendly `.htm` files.

## Run

```cmd
python main.py
```

## UI Notes

- The app uses a compact two-phase PyQt interface.
- The main window uses a centered custom frameless title bar with minimize and close controls.
- The setup phase collects credentials, court selections, docket numbers, and headless mode.
- The automation phase replaces the setup controls while the bot runs.
- `Save ID` stores only the Lexis ID in the user's Windows app-data settings file. Passwords are never saved.
- The circuit-court dropdown supports multiple checked courts.
- Double-click the docket-number field to open a larger docket-list panel. With one court selected, the panel shows one docket list. With multiple courts selected, it shows a separate docket list for each court.
- Click the small triangle beside `Docket Numbers` to load dockets from a PDF folder. The app scans PDF filenames, skips dockets that already have a matching counsel file in that folder tree, and groups the remaining dockets by detected court markers such as `CA4`, `CA9`, `CADC`, `USAP06`, or `USAP6U`. Folder names can also include publication hints: `CA6_UNPUB` routes output to `USAP6U`, while `CA6_PUB` routes output to `USAP06`. If a PDF has no court marker, select exactly one court first and the app will use it as the fallback.
- Docket input accepts commas, semicolons, or new lines. Values are normalized before search.
- Pasted docket lists can include court suffixes such as `_CA4`, `_CA8`, `_CA9`, `_CA11`, or `_CADC`. When suffixes are detected, the app automatically selects the matching courts, groups the docket numbers, and strips the suffixes before searching Lexis.
- Feedback-form rows using `USAPDCORD` route to D.C. Circuit. A `u` immediately after the docket in the filename, such as `LDC_SMD_23-5034u_E2E_PCQ.pdf`, routes output to `USADCU`; otherwise output routes to `USAPDC`. The `Renamed` sheet keeps `USAPDCORD` in the court column and removes that `u` marker from filenames.
- Lexis accepts up to 10 docket numbers per search, so the app automatically splits larger court lists into 10-docket batches while keeping all output in the same court folder.
- Before CourtLink collection starts, the app checks IRT for existing counsel files using the routed court code, docket number, expected counsel filename when available, and a two-year received-date window ending today. Dockets already found in IRT are skipped and documented in the `Renamed` sheet with `RECYCLED COUNSEL LNI` and `MOST RECENT RECEIVED DATE`. When multiple related counsel filenames exist, the app verifies candidate content before recycling the LNI; if normal page text cannot be verified, it can use OCR as a fallback when Tesseract OCR is installed.

## IRT Check Only

Select **IRT Check Only** in the **Mode** dropdown, enter or paste your courts and dockets (or load a PDF folder), and click **Check IRT**.

- Checks by court and docket number with the `*counsel*` filename filter. It checks the circuit's published and unpublished IRT codes and uses the most recently received matching record.
- Stops after IRT checking, including when no match exists. It does not start CourtLink, log into Lexis, or download new counsel documents. Lexis ID/password are not required in this mode; your normal IRT access is still required.
- Uses the existing two-year received-date window ending today. **Not found** means no matching counsel was found in that window.
- Writes an **IRT Check** sheet in the run's `Report_*.xlsx` workbook with court, docket, Found/Not found status, recycled counsel LNI, most recent received date, and matching IRT court code. Failed, cancelled, and unattempted checks are labeled separately.
- Folder loading includes PDFs even when matching local counsel files exist. Switching modes reloads a previously loaded folder using that mode's rules. For folder-loaded runs, only the report is copied back to the source folder in this mode.
- Switch back to **Counsel Collection** for the usual IRT preflight followed by CourtLink collection of missing counsel.

Example smart paste:

```text
25-2531_CA8
25-1144_CA4
25-3714_CA6
25-3974_CA9
25-13746_CA11
```

## Output

Each run writes to its own timestamped folder under Downloads:

```text
%USERPROFILE%\Downloads\Counsel Collector\YYYY-MM-DD_HHMMSS\
```

Each run folder contains:

- One subfolder per selected court. Counsel `.htm` files are saved inside their court subfolder.
- `Log_YYYY-MM-DD_HHMMSS.log`.
- `Output_YYYY-MM-DD_HHMMSS.xlsx`.
- `debug/` snapshots only when a search/result/scrape issue occurs.

For directory-loaded runs, the app also copies the counsel output folder(s) and `Output_YYYY-MM-DD_HHMMSS.xlsx` back to the selected source directory after the run. Existing files are not overwritten; copied duplicates receive the next available suffix.

Example:

```text
%USERPROFILE%\Downloads\Counsel Collector\YYYY-MM-DD_HHMMSS\
|-- 4th Circuit Court\
|   `-- LDC_SMD_25-1144counsel_E2E.htm
|-- 11th Circuit Court\
|   `-- LDC_SMD_24-11772counsel_E2E.htm
|-- debug\
|-- Output_YYYY-MM-DD_HHMMSS.xlsx
`-- Log_YYYY-MM-DD_HHMMSS.log
```

Multi-court runs reuse one browser session and one run folder.

## Project Structure

```text
Counsel Collector/
|-- main.py                         # Main app entry point
|-- requirements.txt                # Runtime dependencies
|-- automation/
|   |-- artifacts.py                # Per-run folder, log, and Excel summary writer
|   |-- browser_session.py          # Chrome/Selenium setup
|   |-- irt_duplicate_checker.py    # IRT duplicate preflight for existing counsel files
|   |-- login.py                    # Lexis login and login check
|   |-- workflow.py                 # CourtLink search/result workflow
|   `-- counsel_parser.py           # Lexis document parsing and HTML output
|-- config/
|   `-- app_settings.py             # URLs, court IDs, selectors, paths
|-- gui/
|   `-- main_window.py              # Compact PyQt GUI and docket popup
`-- utils/
    |-- docket_input.py             # Docket normalization and smart court-suffix parser
    |-- user_settings.py            # Saved Lexis ID setting
    `-- docket_filenames.py         # Filename-to-docket helper
```

## Supported Courts

Supported courts are configured in `config/app_settings.py`.

Currently configured:

- 1st Circuit Court
- 2nd Circuit Court
- 3rd Circuit Court
- 4th Circuit Court
- 5th Circuit Court
- 6th Circuit Court
- 7th Circuit Court
- 8th Circuit Court
- 9th Circuit Court
- 10th Circuit Court
- 11th Circuit Court
- DC Circuit Court

## Dependencies

Install with:

```cmd
pip install -r requirements.txt
```

The optional OCR fallback requires the Tesseract OCR engine to be installed on the machine. If `tesseract.exe` is not on `PATH`, set the `TESSERACT_CMD` environment variable to its full path.
