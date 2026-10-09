"""
Escritura de archivos Excel (.xlsx) sin dependencias externas: varias hojas con cabecera fija, filtros, anchos de
columna y formatos numéricos. Suficiente para los informes; no pretende cubrir el formato completo.

Uso: write_xlsx(ruta, [Sheet("Resumen", [("Cliente", "text"), ("GB", "decimal")], filas), ...])
Tipos de columna: text, wrap (texto en varias líneas), int, decimal, percent.
"""
import math
import re
import zipfile
from xml.sax.saxutils import escape

_ILLEGAL_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
_INVALID_SHEET = re.compile(r"[\[\]:*?/\\]")
MAX_CELL_TEXT = 32767
STYLE = {"text": 0, "header": 1, "int": 2, "decimal": 3, "percent": 4, "wrap": 5}

_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
    '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
    '{sheets}'
    '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
    '</Types>'
)
_ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
    '</Relationships>'
)
_CORE = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
    '<dc:title>{title}</dc:title><dc:creator>Splunk Manager</dc:creator>'
    '</cp:coreProperties>'
)
_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<numFmts count="1"><numFmt numFmtId="164" formatCode="#,##0.00"/></numFmts>'
    '<fonts count="2">'
    '<font><sz val="11"/><name val="Calibri"/><family val="2"/></font>'
    '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/><family val="2"/></font>'
    '</fonts>'
    '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FF2A78D6"/><bgColor indexed="64"/></patternFill></fill></fills>'
    '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="6">'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>'
    '<xf numFmtId="3" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
    '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
    '<xf numFmtId="10" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>'
    '</cellXfs>'
    '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
    '</styleSheet>'
)


class Sheet:
    def __init__(self, name, columns, rows):
        """columns: [(título, tipo)]; rows: listas de valores en el orden de las columnas."""
        self.name = name
        self.columns = columns
        self.rows = rows


def column_letter(index):
    """0 -> A, 25 -> Z, 26 -> AA"""
    letters = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def _clean_text(value):
    text = _ILLEGAL_XML.sub("", str(value))
    return text[:MAX_CELL_TEXT]


def _text_cell(ref, text, style):
    space = ' xml:space="preserve"' if text != text.strip() or "\n" in text else ""
    return f'<c r="{ref}" t="inlineStr" s="{style}"><is><t{space}>{escape(text)}</t></is></c>'


def _cell(ref, value, kind):
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return f'<c r="{ref}" t="b"><v>{int(value)}</v></c>'
    if isinstance(value, (int, float)) and kind in ("int", "decimal", "percent"):
        if isinstance(value, float) and not math.isfinite(value):
            return _text_cell(ref, str(value), STYLE["text"])
        return f'<c r="{ref}" s="{STYLE[kind]}"><v>{value!r}</v></c>'
    return _text_cell(ref, _clean_text(value), STYLE["wrap" if kind == "wrap" else "text"])


def _width(title, values, kind):
    longest = max([len(str(title))] + [max((len(line) for line in str(v).split("\n")), default=0)
                                       for v in values[:500] if v is not None])
    if kind in ("int", "decimal", "percent"):
        longest = max(longest, 10)
    return min(max(longest + 2, 8), 70 if kind == "wrap" else 50)


def _sheet_xml(sheet):
    columns = sheet.columns
    last_col = column_letter(max(len(columns) - 1, 0))
    last_row = len(sheet.rows) + 1
    cols = "".join(
        f'<col min="{i + 1}" max="{i + 1}" width="{_width(title, [r[i] if i < len(r) else None for r in sheet.rows], kind)}" customWidth="1"/>'
        for i, (title, kind) in enumerate(columns))
    rows = ['<row r="1">' + "".join(_text_cell(f"{column_letter(i)}1", _clean_text(title), STYLE["header"])
                                    for i, (title, _) in enumerate(columns)) + "</row>"]
    for r, values in enumerate(sheet.rows, start=2):
        cells = "".join(_cell(f"{column_letter(i)}{r}", values[i] if i < len(values) else None, kind)
                        for i, (_, kind) in enumerate(columns))
        rows.append(f'<row r="{r}">{cells}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<dimension ref="A1:{last_col}{last_row}"/>'
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
        '</sheetView></sheetViews>'
        '<sheetFormatPr defaultRowHeight="15"/>'
        f'<cols>{cols}</cols>'
        f'<sheetData>{"".join(rows)}</sheetData>'
        f'<autoFilter ref="A1:{last_col}{last_row}"/>'
        '</worksheet>'
    )


def _sheet_names(sheets):
    names, used = [], set()
    for sheet in sheets:
        base = _INVALID_SHEET.sub(" ", str(sheet.name)).strip().strip("'")[:31] or "Hoja"
        name, counter = base, 2
        while name.lower() in used:
            suffix = f" ({counter})"
            name = base[:31 - len(suffix)] + suffix
            counter += 1
        used.add(name.lower())
        names.append(name)
    return names


def write_xlsx(path, sheets, title="Informe"):
    names = _sheet_names(sheets)
    sheet_entries = "".join(f'<sheet name="{escape(n, {chr(34): "&quot;"})}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
                            for i, n in enumerate(names))
    defined = "".join(
        f'<definedName name="_xlnm._FilterDatabase" localSheetId="{i}" hidden="1">'
        f"{escape(chr(39) + n.replace(chr(39), chr(39) * 2) + chr(39))}!$A$1:${column_letter(max(len(s.columns) - 1, 0))}${len(s.rows) + 1}"
        '</definedName>'
        for i, (n, s) in enumerate(zip(names, sheets)))
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<bookViews><workbookView/></bookViews>'
        f'<sheets>{sheet_entries}</sheets>'
        f'<definedNames>{defined}</definedNames>'
        '</workbook>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                  f'Target="worksheets/sheet{i + 1}.xml"/>' for i in range(len(sheets)))
        + f'<Relationship Id="rId{len(sheets) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        '</Relationships>'
    )
    content_types = _CONTENT_TYPES.format(sheets="".join(
        f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(len(sheets))))

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", _ROOT_RELS)
        z.writestr("docProps/core.xml", _CORE.format(title=escape(_clean_text(title))))
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        z.writestr("xl/styles.xml", _STYLES)
        for i, sheet in enumerate(sheets):
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", _sheet_xml(sheet))
