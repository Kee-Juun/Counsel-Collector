import re


COURT_CODE_TO_LABEL = {
    "CA1": "1st Circuit Court",
    "CA2": "2nd Circuit Court",
    "CA3": "3rd Circuit Court",
    "CA4": "4th Circuit Court",
    "CA5": "5th Circuit Court",
    "CA6": "6th Circuit Court",
    "CA7": "7th Circuit Court",
    "CA8": "8th Circuit Court",
    "CA9": "9th Circuit Court",
    "CA10": "10th Circuit Court",
    "CA11": "11th Circuit Court",
    "CADC": "DC Circuit Court",
    "DC": "DC Circuit Court",
}

COURT_LABEL_TO_USAP_PUBLISHED = {
    "1st Circuit Court": "USAP01",
    "2nd Circuit Court": "USAP02",
    "3rd Circuit Court": "USAP03",
    "4th Circuit Court": "USAP04",
    "5th Circuit Court": "USAP05",
    "6th Circuit Court": "USAP06",
    "7th Circuit Court": "USAP07",
    "8th Circuit Court": "USAP08",
    "9th Circuit Court": "USAP09",
    "10th Circuit Court": "USAP10",
    "11th Circuit Court": "USAP11",
    "DC Circuit Court": "USAPDC",
}

USAP_CODE_RE = re.compile(
    r"\b(?:(?P<usadcu>USADCU)|USAP(?P<court>0?[1-9]|10|11|DCORD|DC)(?P<unpublished>U?))\b",
    re.IGNORECASE,
)
USAP_SEGMENT_RE = re.compile(
    r"(?P<code>\b(?:USADCU|USAP(?:0?[1-9]|10|11|DCORD|DC)U?)\b)"
    r"(?P<body>.*?)(?=\b(?:USADCU|USAP(?:0?[1-9]|10|11|DCORD|DC)U?)\b|$)",
    re.IGNORECASE | re.DOTALL,
)

CODED_DOCKET_RE = re.compile(
    r"^(?P<docket>.+?)[_-](?P<code>CA(?:1[01]|[1-9])|CA[_-]?DC|CADC|DC)$",
    re.IGNORECASE,
)

DOCKET_NUMBER_RE = re.compile(r"(?<!\d)\d{2,4}-\d{1,6}(?!\d)")
FEEDBACK_FILENAME_RE = re.compile(r"\b(?:LDC|DC)_SMD_[^\s,;]+\.(?:pdf|html?|HTML?)\b", re.IGNORECASE)
CA_FEEDBACK_ROW_RE = re.compile(
    r"(?<!\S)(?P<code>CA(?:1[01]|[1-9])U?|CA[_-]?DCU?|CADCU?|DCU?)"
    r"\s+(?P<filename>[^\s,;]+\.pdf)\b",
    re.IGNORECASE,
)
SMD_CA_FILENAME_RE = re.compile(
    r"\b(?P<filename>[^\s,;]*?SMD_(?P<docket>\d{2,4}-\d{1,6})_"
    r"(?P<code>CA(?:1[01]|[1-9])|CA[_-]?DC|CADC|DC)(?=[_\-\s.])"
    r"[^\s,;]*\.pdf)\b",
    re.IGNORECASE,
)
DCORD_UNPUBLISHED_FLAG_RE = re.compile(
    r"(?P<base>(?<!\d)\d{2,4}-\d{1,6}(?:counsel(?:-\d+)?)?)u(?=(_|\.|$))",
    re.IGNORECASE,
)
CA_FILENAME_COURT_RE = re.compile(
    r"^(?P<prefix>.*?(?<!\d)\d{2,4}-\d{1,6}[_\-\s](?:CA(?:1[01]|[1-9])|CA[_-]?DC|CADC))(?=$|[_\-\s.])",
    re.IGNORECASE,
)


def extract_docket_numbers(text):
    dockets = []
    seen = set()
    for match in DOCKET_NUMBER_RE.finditer(text or ""):
        docket = match.group(0)
        if docket in seen:
            continue
        seen.add(docket)
        dockets.append(docket)
    return dockets


def _ordinal_court_label(court_number):
    suffixes = {1: "st", 2: "nd", 3: "rd"}
    suffix = suffixes.get(court_number, "th")
    return f"{court_number}{suffix} Circuit Court"


def canonical_ca_code(value):
    raw = str(value or "").upper().replace("_", "").replace("-", "")
    if raw in {"DC", "DCU"}:
        return raw
    if raw in {"CADC", "CADCU"}:
        return raw

    match = re.fullmatch(r"CA(?P<court>1[01]|[1-9])(?P<unpublished>U?)", raw)
    if match:
        return f"CA{int(match.group('court'))}{match.group('unpublished')}"

    return ""


def ca_code_to_court_label(value):
    code = canonical_ca_code(value)
    if not code:
        return ""
    lookup_code = code[:-1] if code.endswith("U") else code
    return COURT_CODE_TO_LABEL.get(lookup_code, "")


def output_folder_for_ca_code(value):
    code = canonical_ca_code(value)
    court_label = ca_code_to_court_label(code)
    if not court_label:
        return ""
    if code.endswith("U"):
        return unpublished_output_folder_for_court(court_label)
    return default_output_folder_for_court(court_label)


def canonical_usap_code(value):
    match = USAP_CODE_RE.search(value or "")
    if not match:
        return ""

    if match.group("usadcu"):
        return "USADCU"

    court = match.group("court").upper()
    unpublished = bool(match.group("unpublished"))
    if court == "DCORD":
        return "USAPDCORD"
    if court == "DC":
        return "USADCU" if unpublished else "USAPDC"

    court_number = int(court)
    if unpublished:
        return f"USAP{court_number}U"
    return f"USAP{court_number:02d}" if court_number < 10 else f"USAP{court_number}"


def usap_code_to_court_label(value):
    code = canonical_usap_code(value)
    if not code:
        return ""
    if code.startswith("USAPDC") or code == "USADCU":
        return "DC Circuit Court"

    match = re.match(r"USAP0?(?P<court>[1-9]|10|11)U?$", code, re.IGNORECASE)
    if not match:
        return ""
    return _ordinal_court_label(int(match.group("court")))


def default_output_folder_for_court(court_label):
    return COURT_LABEL_TO_USAP_PUBLISHED.get(court_label, court_label)


def unpublished_output_folder_for_court(court_label):
    published = default_output_folder_for_court(court_label)
    if court_label == "DC Circuit Court":
        return "USADCU"

    match = re.match(r"USAP0?(?P<court>[1-9]|10|11)$", published, re.IGNORECASE)
    if not match:
        return published

    return f"USAP{int(match.group('court'))}U"


def _is_usapdcord(code):
    return canonical_usap_code(code) == "USAPDCORD"


def _has_dcord_unpublished_flag(filename):
    return bool(DCORD_UNPUBLISHED_FLAG_RE.search(filename or ""))


def _normalize_dcord_unpublished_flag(code, value):
    if not _is_usapdcord(code):
        return value
    return DCORD_UNPUBLISHED_FLAG_RE.sub(r"\g<base>", value or "")


def _dcord_output_folder(code, filename="", fallback=""):
    if not _is_usapdcord(code):
        return code
    if _has_dcord_unpublished_flag(filename):
        return "USADCU"
    return fallback or "USAPDC"


def output_folder_for_usap_code(value, filename="", fallback=""):
    code = canonical_usap_code(value)
    if not code:
        return fallback
    return _dcord_output_folder(code, filename, fallback)


def parse_usap_docket_rows(text):
    grouped = {}
    grouped_filenames = {}
    current_main_by_code = {}
    raw_text = text or ""
    matches = list(USAP_SEGMENT_RE.finditer(raw_text))

    if not matches:
        return [], normalize_dockets(raw_text)

    plain = normalize_dockets(raw_text[:matches[0].start()])

    for match in matches:
        code = canonical_usap_code(match.group("code"))
        court_label = usap_code_to_court_label(code)
        if not court_label:
            continue

        body = match.group("body").strip()
        filename_match = FEEDBACK_FILENAME_RE.search(body)
        dockets = []
        output_filename = ""

        if filename_match:
            filename = filename_match.group(0)
            if _pcq_parts(filename):
                current_main_by_code[code] = {
                    "filename": filename,
                    "output_folder": _dcord_output_folder(code, filename),
                    "counsel_index": 0,
                }
                continue
            if "counsel" in filename.lower():
                dockets = extract_docket_numbers(filename)
                output_folder = _dcord_output_folder(
                    code,
                    filename,
                    current_main_by_code.get(code, {}).get("output_folder", ""),
                )
                output_filename = _normalize_dcord_unpublished_flag(code, filename)
        elif not body:
            main_info = current_main_by_code.get(code, {})
            main_filename = main_info.get("filename", "")
            dockets = extract_docket_numbers(main_filename)
            output_folder = main_info.get("output_folder") or _dcord_output_folder(code)
            if main_filename and len(dockets) == 1:
                index = main_info.get("counsel_index", 0)
                output_filename = counsel_filename_for_main_document(
                    main_filename,
                    index=index,
                    strip_dcord_unpublished_flag=(
                        canonical_usap_code(code) == "USAPDCORD" and output_folder == "USADCU"
                    ),
                )
                main_info["counsel_index"] = index + 1
        else:
            dockets = extract_docket_numbers(body)
            output_folder = current_main_by_code.get(code, {}).get("output_folder") or _dcord_output_folder(code)
            main_info = current_main_by_code.get(code, {})
            if main_info and len(dockets) == 1 and DOCKET_NUMBER_RE.fullmatch(body):
                index = main_info.get("counsel_index", 0)
                output_filename = counsel_filename_for_main_document(
                    main_info.get("filename", ""),
                    index=index,
                    strip_dcord_unpublished_flag=(
                        canonical_usap_code(code) == "USAPDCORD" and output_folder == "USADCU"
                    ),
                )
                main_info["counsel_index"] = index + 1

        if not dockets:
            continue

        key = (court_label, output_folder)
        grouped.setdefault(key, [])
        grouped_filenames.setdefault(key, {})
        for docket in dockets:
            grouped[key].append(docket)
            if output_filename:
                grouped_filenames[key].setdefault(docket, output_filename)

    jobs = []
    for (court_label, output_folder), dockets in grouped.items():
        normalized_dockets = merge_dockets(", ".join(dockets))
        if normalized_dockets:
            jobs.append(
                {
                    "court_label": court_label,
                    "docket_numbers": normalized_dockets,
                    "output_folder": output_folder,
                    "docket_output_filenames": grouped_filenames.get((court_label, output_folder), {}),
                }
            )

    return jobs, plain


def _feedback_segments(text):
    raw_text = text or ""
    matches = list(USAP_SEGMENT_RE.finditer(raw_text))
    for match in matches:
        code = canonical_usap_code(match.group("code"))
        court = usap_code_to_court_label(code)
        body = match.group("body").strip()
        if code and court:
            yield code, body


def _feedback_value(body):
    filename_match = FEEDBACK_FILENAME_RE.search(body)
    if filename_match:
        return filename_match.group(0)

    docket_match = DOCKET_NUMBER_RE.search(body)
    if docket_match:
        return docket_match.group(0)

    return ""


def _pcq_parts(filename):
    match = re.match(
        r"(?P<prefix>.*?(?<!\d)\d{2,4}-\d{1,6})(?P<marker>.*?)_PCQ\.pdf$",
        filename,
        re.IGNORECASE,
    )
    if not match:
        return None
    return {
        "prefix": match.group("prefix"),
        "marker": match.group("marker"),
    }


def _counsel_filename(main_parts, index):
    suffix = "" if index == 0 else f"-{index}"
    return f"{main_parts['prefix']}counsel{suffix}{main_parts['marker']}.htm"


def counsel_filename_for_main_document(filename, index=0, strip_dcord_unpublished_flag=False):
    basename = str(filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    if strip_dcord_unpublished_flag:
        basename = _normalize_dcord_unpublished_flag("USAPDCORD", basename)

    pcq_parts = _pcq_parts(basename)
    if pcq_parts:
        return _counsel_filename(pcq_parts, index)

    suffix = "" if index == 0 else f"-{index}"
    stem = re.sub(r"\.pdf$", "", basename, flags=re.IGNORECASE)
    court_match = CA_FILENAME_COURT_RE.match(stem)
    if court_match:
        return f"{court_match.group('prefix')}_Counsel{suffix}.htm"

    docket_match = re.match(r"(?P<prefix>.*?(?<!\d)\d{2,4}-\d{1,6})(?P<marker>.*)$", stem)
    if docket_match:
        marker = re.sub(r"(?:^|_)PCQ$", "", docket_match.group("marker"), flags=re.IGNORECASE)
        return f"{docket_match.group('prefix')}counsel{suffix}{marker}.htm"

    return f"{stem}counsel{suffix}.htm"


def _docket_from_smd_ca_filename(filename):
    match = SMD_CA_FILENAME_RE.search(filename or "")
    if match:
        return match.group("docket")

    dockets = extract_docket_numbers(filename)
    return dockets[0] if dockets else ""


def parse_ca_feedback_rows(text):
    grouped = {}
    grouped_filenames = {}
    renamed_rows = []
    seen_files = set()

    for match in CA_FEEDBACK_ROW_RE.finditer(text or ""):
        raw_code = match.group("code")
        filename = match.group("filename").replace("\\", "/").rsplit("/", 1)[-1]
        if "counsel" in filename.lower():
            continue

        court_label = ca_code_to_court_label(raw_code)
        output_folder = output_folder_for_ca_code(raw_code)
        docket = _docket_from_smd_ca_filename(filename)
        if not court_label or not output_folder or not docket:
            continue

        key = (court_label, output_folder)
        grouped.setdefault(key, [])
        grouped_filenames.setdefault(key, {})
        grouped[key].append(docket)
        grouped_filenames[key].setdefault(docket, counsel_filename_for_main_document(filename))

        display_code = canonical_ca_code(raw_code) or raw_code.upper()
        file_key = (display_code, filename.lower())
        if file_key in seen_files:
            continue
        seen_files.add(file_key)

        counsel_filename = grouped_filenames[key][docket]
        renamed_rows.append(
            {
                "COURT": display_code,
                "FEEDBACK FORM FILENAME": filename,
                "RENAMED": filename,
            }
        )
        renamed_rows.append(
            {
                "COURT": display_code,
                "FEEDBACK FORM FILENAME": "",
                "RENAMED": counsel_filename,
            }
        )

    jobs = []
    for (court_label, output_folder), dockets in grouped.items():
        normalized_dockets = merge_dockets(", ".join(dockets))
        if normalized_dockets:
            jobs.append(
                {
                    "court_label": court_label,
                    "docket_numbers": normalized_dockets,
                    "output_folder": output_folder,
                    "docket_output_filenames": grouped_filenames.get((court_label, output_folder), {}),
                }
            )

    return jobs, renamed_rows


def _counsel_index_for_filename(filename, main_parts):
    marker = re.escape(main_parts["marker"])
    prefix = re.escape(main_parts["prefix"])
    match = re.match(
        rf"^{prefix}counsel(?:-(?P<index>\d+))?{marker}\.html?$",
        filename,
        re.IGNORECASE,
    )
    if not match:
        return None
    return int(match.group("index") or 0)


def parse_feedback_renamed_rows(text):
    rows = []
    current_main = {}
    counsel_counts = {}

    for code, body in _feedback_segments(text):
        value = _feedback_value(body)

        normalized_value = _normalize_dcord_unpublished_flag(code, value)
        row_code = code

        renamed = normalized_value
        pcq_parts = _pcq_parts(normalized_value)
        if pcq_parts:
            current_main[code] = pcq_parts
            counsel_counts[pcq_parts["prefix"]] = 0
        elif not value or DOCKET_NUMBER_RE.fullmatch(value):
            main_parts = current_main.get(code)
            if main_parts:
                key = main_parts["prefix"]
                index = counsel_counts.get(key, 0)
                renamed = _counsel_filename(main_parts, index)
                counsel_counts[key] = index + 1
            elif not value:
                continue
            else:
                renamed = f"LDC_SMD_{value}counsel.htm"
        elif code in current_main:
            explicit_index = _counsel_index_for_filename(normalized_value, current_main[code])
            if explicit_index is not None:
                key = current_main[code]["prefix"]
                counsel_counts[key] = max(counsel_counts.get(key, 0), explicit_index + 1)

        rows.append(
            {
                "COURT": row_code,
                "FEEDBACK FORM FILENAME": value,
                "RENAMED": renamed,
            }
        )

    return rows


def _raw_docket_parts(text):
    return [part.strip() for part in re.split(r"[,;\n]+", text or "") if part.strip()]


def _normalized_parts(text):
    extracted = extract_docket_numbers(text)
    if extracted:
        return extracted
    return _raw_docket_parts(text)


def normalize_dockets(text):
    return ", ".join(_normalized_parts(text))


def docket_lines(text):
    return "\n".join(_normalized_parts(text))


def docket_count(text):
    return len(_normalized_parts(text))


def merge_dockets(*texts):
    merged = []
    seen = set()
    for text in texts:
        for docket in _normalized_parts(text):
            if not docket or docket in seen:
                continue
            seen.add(docket)
            merged.append(docket)
    return ", ".join(merged)


def _canonical_court_code(code):
    return code.upper().replace("_", "").replace("-", "")


def parse_court_coded_dockets(text):
    grouped = {}
    plain = []

    for token in re.split(r"[,;\s]+", text):
        token = token.strip()
        if not token:
            continue

        match = CODED_DOCKET_RE.match(token)
        if not match:
            plain.extend(extract_docket_numbers(token))
            continue

        court_label = COURT_CODE_TO_LABEL.get(_canonical_court_code(match.group("code")))
        docket = normalize_dockets(match.group("docket"))
        if court_label and docket:
            grouped.setdefault(court_label, []).append(docket)
        else:
            plain.extend(extract_docket_numbers(token))

    return {
        court_label: merge_dockets(", ".join(dockets))
        for court_label, dockets in grouped.items()
    }, merge_dockets(", ".join(plain))
