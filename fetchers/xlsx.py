"""極簡 .xlsx 讀取器（只用標準函式庫），給摩根投信的 Excel 用。

回傳 {工作表名稱: [[cell, ...], ...]}，儲存格值一律是字串或 None（不轉數字，由呼叫端解析）。
支援 sharedStrings、inlineStr 與一般數值；不處理公式快取以外的東西。
"""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _text(node) -> str:
    return "".join(t.text or "" for t in node.iter(f"{{{NS}}}t"))


def read_xlsx(data: bytes) -> dict[str, list[list[str | None]]]:
    if not data.startswith(b"PK"):
        raise ValueError("not an xlsx file (missing PK signature)")
    z = zipfile.ZipFile(io.BytesIO(data))
    names = set(z.namelist())

    shared: list[str] = []
    if "xl/sharedStrings.xml" in names:
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(f"{{{NS}}}si"):
            shared.append(_text(si))

    rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    out: dict[str, list[list[str | None]]] = {}
    sheets = wb.find(f"{{{NS}}}sheets")
    for sh in (sheets if sheets is not None else []):
        target = rels.get(sh.get(f"{{{REL_NS}}}id"), "")
        target = target.lstrip("/")
        if not target.startswith("xl/"):
            target = "xl/" + target
        rows: list[list[str | None]] = []
        for row in ET.fromstring(z.read(target)).iter(f"{{{NS}}}row"):
            cells: list[str | None] = []
            for c in row.findall(f"{{{NS}}}c"):
                t = c.get("t")
                v = c.find(f"{{{NS}}}v")
                if t == "s" and v is not None and v.text is not None:
                    cells.append(shared[int(v.text)])
                elif t == "inlineStr":
                    is_ = c.find(f"{{{NS}}}is")
                    cells.append(_text(is_) if is_ is not None else None)
                else:
                    cells.append(v.text if v is not None else None)
            rows.append(cells)
        out[sh.get("name") or f"sheet{len(out) + 1}"] = rows
    return out
