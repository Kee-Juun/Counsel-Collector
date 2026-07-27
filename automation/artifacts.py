from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import shutil
from xml.sax.saxutils import escape as xml_escape
from zipfile import ZIP_DEFLATED, ZipFile

from config.app_settings import RUNS_ROOT

try:
    from colorama import Fore, Style, init as colorama_init

    colorama_init(autoreset=True)
    COLORAMA_AVAILABLE = True
except ImportError:
    Fore = Style = None
    COLORAMA_AVAILABLE = False


SUMMARY_COLUMNS = (
    "Court",
    "Status",
    "Case Number",
    "Output File",
    "Result URL",
    "Message",
)

RENAMED_COLUMNS = (
    "COURT",
    "FEEDBACK FORM FILENAME",
    "RENAMED",
    "RECYCLED COUNSEL LNI",
    "MOST RECENT RECEIVED DATE",
)


COLUMN_WIDTH_LIMITS = {
    "Court": (12, 24),
    "Status": (10, 16),
    "Case Number": (12, 22),
    "Output File": (18, 58),
    "Result URL": (20, 72),
    "Message": (16, 72),
    "COURT": (10, 16),
    "FEEDBACK FORM FILENAME": (24, 42),
    "RENAMED": (24, 42),
    "RECYCLED COUNSEL LNI": (34, 96),
    "MOST RECENT RECEIVED DATE": (24, 72),
}


def _console_color(level, message):
    if not COLORAMA_AVAILABLE:
        return ""

    level = str(level or "").upper()
    message = str(message or "")
    normalized = message.lower()

    if level == "ERROR":
        return Fore.RED + Style.BRIGHT
    if level == "WARNING":
        return Fore.YELLOW + Style.BRIGHT
    if "--------------" in message:
        return Fore.CYAN + Style.BRIGHT
    if any(marker in normalized for marker in ("saved ", "saved:", "complete", "completed", "success")):
        return Fore.GREEN
    return ""


def _colored_console_line(line, level, message):
    color = _console_color(level, message)
    if not color:
        return line
    return f"{color}{line}{Style.RESET_ALL}"


def _next_available_dir(path):
    if not path.exists():
        return path

    for index in range(2, 1000):
        candidate = path.with_name(f"{path.name}_{index}")
        if not candidate.exists():
            return candidate
    return path


def _next_available_file(path):
    if not path.exists():
        return path

    for index in range(2, 1000):
        candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    return path


def _safe_folder_name(value):
    cleaned = "".join(char if char.isalnum() or char in " ._-" else "_" for char in value.strip())
    cleaned = " ".join(cleaned.split())
    return cleaned.strip(" ._") or "Court"


def _column_name(index):
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _cell(row_index, column_index, value, style_id=0):
    ref = f"{_column_name(column_index)}{row_index}"
    text = "" if value is None else str(value)
    space = ' xml:space="preserve"' if text != text.strip() else ""
    style = f' s="{style_id}"' if style_id else ""
    return f'<c r="{ref}"{style} t="inlineStr"><is><t{space}>{xml_escape(text)}</t></is></c>'


def _display_width(value):
    text = "" if value is None else str(value)
    if not text:
        return 0
    return max(len(part) for part in text.splitlines())


def _column_widths(rows):
    widths = []
    for column_index, header in enumerate(rows[0], start=1):
        min_width, max_width = COLUMN_WIDTH_LIMITS.get(header, (10, 60))
        content_width = max(
            (_display_width(row[column_index - 1]) for row in rows if len(row) >= column_index),
            default=0,
        )
        width = min(max(content_width + 2, min_width), max_width)
        widths.append(width)
    return widths


def _cols_xml(rows):
    columns = []
    for index, width in enumerate(_column_widths(rows), start=1):
        columns.append(f'<col min="{index}" max="{index}" width="{width}" customWidth="1"/>')
    return f"<cols>{''.join(columns)}</cols>"


def _worksheet_xml(rows):
    row_xml = []
    for row_index, row in enumerate(rows, start=1):
        cells = "".join(
            _cell(
                row_index,
                column_index,
                value,
                style_id=2 if row_index == 1 else 1,
            )
            for column_index, value in enumerate(row, start=1)
        )
        row_xml.append(f'<row r="{row_index}">{cells}</row>')

    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
    <sheetViews><sheetView workbookViewId="0"/></sheetViews>
    <sheetFormatPr defaultRowHeight="15"/>
    {_cols_xml(rows)}
    <sheetData>{"".join(row_xml)}</sheetData>
</worksheet>"""


def _styles_xml():
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
    <fonts count="2">
        <font><sz val="11"/><name val="Calibri"/><family val="2"/></font>
        <font><b/><color rgb="FF03131A"/><sz val="11"/><name val="Calibri"/><family val="2"/></font>
    </fonts>
    <fills count="3">
        <fill><patternFill patternType="none"/></fill>
        <fill><patternFill patternType="gray125"/></fill>
        <fill>
            <gradientFill degree="0">
                <stop position="0"><color rgb="FF6FF7FF"/></stop>
                <stop position="0.28"><color rgb="FFE0F7FF"/></stop>
                <stop position="0.62"><color rgb="FF8FA6FF"/></stop>
                <stop position="1"><color rgb="FFFF5EF7"/></stop>
            </gradientFill>
        </fill>
    </fills>
    <borders count="3">
        <border><left/><right/><top/><bottom/><diagonal/></border>
        <border>
            <left style="thin"><color rgb="FFD9D9D9"/></left>
            <right style="thin"><color rgb="FFD9D9D9"/></right>
            <top style="thin"><color rgb="FFD9D9D9"/></top>
            <bottom style="thin"><color rgb="FFD9D9D9"/></bottom>
            <diagonal/>
        </border>
        <border>
            <left style="thin"><color rgb="FF000000"/></left>
            <right style="thin"><color rgb="FF000000"/></right>
            <top style="thin"><color rgb="FF000000"/></top>
            <bottom style="thin"><color rgb="FF000000"/></bottom>
            <diagonal/>
        </border>
    </borders>
    <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
    <cellXfs count="3">
        <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
        <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1">
            <alignment horizontal="center" vertical="center"/>
        </xf>
        <xf numFmtId="0" fontId="1" fillId="2" borderId="2" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1">
            <alignment horizontal="center" vertical="center"/>
        </xf>
    </cellXfs>
    <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""


def _safe_sheet_name(value, fallback):
    invalid_chars = set("[]:*?/\\")
    cleaned = "".join("_" if char in invalid_chars else char for char in str(value).strip())
    return (cleaned or fallback)[:31]


def _normalize_sheets(sheets):
    if isinstance(sheets, dict):
        items = list(sheets.items())
    elif sheets and isinstance(sheets[0], tuple):
        items = list(sheets)
    else:
        items = [("Results", sheets)]

    normalized = []
    for index, (name, rows) in enumerate(items, start=1):
        rows = list(rows)
        if not rows:
            rows = [SUMMARY_COLUMNS]
        normalized.append((_safe_sheet_name(name, f"Sheet{index}"), rows))
    return normalized


def write_xlsx(path, sheets):
    path.parent.mkdir(parents=True, exist_ok=True)
    sheets = _normalize_sheets(sheets)

    worksheet_overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
        f'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for index, _sheet in enumerate(sheets, start=1)
    )
    sheet_xml = "".join(
        f'<sheet name="{xml_escape(name)}" sheetId="{index}" r:id="rId{index}"/>'
        for index, (name, _rows) in enumerate(sheets, start=1)
    )
    relationship_xml = "".join(
        f'<Relationship Id="rId{index}" '
        f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        f'Target="worksheets/sheet{index}.xml"/>'
        for index, _sheet in enumerate(sheets, start=1)
    )
    styles_relationship_id = len(sheets) + 1

    with ZipFile(path, "w", ZIP_DEFLATED) as workbook:
        workbook.writestr(
            "[Content_Types].xml",
            f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
    <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
    <Default Extension="xml" ContentType="application/xml"/>
    <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
    <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
    {worksheet_overrides}
</Types>""",
        )
        workbook.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
    <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""",
        )
        workbook.writestr(
            "xl/workbook.xml",
            f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
    <sheets>
        {sheet_xml}
    </sheets>
</workbook>""",
        )
        workbook.writestr(
            "xl/_rels/workbook.xml.rels",
            f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
    {relationship_xml}
    <Relationship Id="rId{styles_relationship_id}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>""",
        )
        workbook.writestr("xl/styles.xml", _styles_xml())
        for index, (_name, rows) in enumerate(sheets, start=1):
            workbook.writestr(f"xl/worksheets/sheet{index}.xml", _worksheet_xml(rows))


@dataclass
class RunArtifacts:
    run_dir: Path
    log_path: Path
    summary_path: Path
    debug_dir: Path
    records: list = field(default_factory=list)
    renamed_records: list = field(default_factory=list)

    @classmethod
    def create(cls):
        RUNS_ROOT.mkdir(parents=True, exist_ok=True)
        run_name = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        run_dir = _next_available_dir(RUNS_ROOT / run_name)
        run_label = run_dir.name
        debug_dir = run_dir / "debug"
        artifacts = cls(
            run_dir=run_dir,
            log_path=run_dir / f"Log_{run_label}.log",
            summary_path=run_dir / f"Report_{run_label}.xlsx",
            debug_dir=debug_dir,
        )
        artifacts.log_section("START")
        artifacts.log(f"Run Folder: {run_dir}")
        return artifacts

    def log(self, message, level="INFO"):
        timestamp = datetime.now().strftime("%I:%M:%S %p")
        line = f"{timestamp} - {level.upper()} - {message}"
        print(_colored_console_line(line, level, message))
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as log_file:
            log_file.write(f"{line}\n")

    def log_section(self, title):
        self.log(f"--------------{title}--------------")

    def log_run_summary(self, found, saved, failed, no_results, cancelled=False):
        title = "CANCELLED" if cancelled else "DONE"
        self.log_section(title)
        self.log(f"Found: {found}")
        self.log(f"Saved: {saved}")
        self.log(f"Failed: {failed}")
        self.log(f"No Result Batches: {no_results}")
        self.log_section(title)

    def court_path(self, court_label):
        return self.run_dir / _safe_folder_name(court_label)

    def court_dir(self, court_label):
        output_dir = self.court_path(court_label)
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir

    def add_record(
        self,
        court,
        docket_numbers,
        status="",
        case_number="",
        output_file="",
        result_url="",
        message="",
    ):
        self.records.append(
            {
                "Court": court,
                "Docket Numbers": docket_numbers,
                "Status": status,
                "Case Number": case_number,
                "Output File": output_file,
                "Result URL": result_url,
                "Message": message,
            }
        )

    def save_summary(self):
        rows = [SUMMARY_COLUMNS]
        for record in self.records:
            rows.append([record.get(column, "") for column in SUMMARY_COLUMNS])
        renamed_rows = [RENAMED_COLUMNS]
        for record in self.renamed_records:
            renamed_rows.append([record.get(column, "") for column in RENAMED_COLUMNS])
        sheets = [("Results", rows), ("Renamed", renamed_rows)]
        write_xlsx(self.summary_path, sheets)
        self.log(f"Saved Excel summary: {self.summary_path}")

    def copy_directory_run_outputs(self, target_directory, output_folders):
        target_root = Path(target_directory)
        if not target_root:
            return []

        target_root.mkdir(parents=True, exist_ok=True)
        copied_paths = []
        folder_names = [name for name in dict.fromkeys(output_folders or []) if name]

        for folder_name in folder_names:
            source_dir = self.court_path(folder_name)
            if not source_dir.exists() or not source_dir.is_dir():
                continue

            if target_root.name.lower() == source_dir.name.lower():
                destination_dir = target_root
            else:
                destination_dir = target_root / source_dir.name
                destination_dir.mkdir(parents=True, exist_ok=True)

            copied_any = False
            for source_file in source_dir.rglob("*"):
                if not source_file.is_file():
                    continue
                relative_path = source_file.relative_to(source_dir)
                destination_file = _next_available_file(destination_dir / relative_path)
                destination_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_file, destination_file)
                copied_any = True

            if copied_any:
                copied_paths.append(str(destination_dir))

        if self.summary_path.exists():
            summary_destination = _next_available_file(target_root / self.summary_path.name)
            shutil.copy2(self.summary_path, summary_destination)
            copied_paths.append(str(summary_destination))

        return copied_paths
