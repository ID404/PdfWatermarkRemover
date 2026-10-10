# -*- coding: utf-8 -*-
"""PDF 水印文字去除引擎。

思路：
1. 注解层：删除内容命中水印文字的自由文本 / 图章类注解。
2. 内容流层：若某条页面内容流只用于绘制水印，整条从 /Contents 中摘除，
   并清理仅被它使用的字体、透明度资源。
3. 操作符层：若水印与正文混在同一条内容流里，解析流内的文本绘制操作符
   （Tj / TJ / ' / "），把命中水印文字的字符串摘掉，其余内容原样保留。
4. Form XObject 层：对页面引用的表单 XObject 递归执行第 3 步。

全程只做“减法”，不重排、不重绘正文，处理后自动复检。
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile

import fitz

__all__ = [
    "normalize",
    "scan_document",
    "remove_watermark",
    "RemoveResult",
]

# --------------------------------------------------------------------------
# 文本归一化与字符串解码
# --------------------------------------------------------------------------

_WS_RE = re.compile(r"\s+")


def normalize(text: str, case_sensitive: bool = False) -> str:
    """归一化：去掉所有空白字符，可选忽略大小写。"""
    if not text:
        return ""
    text = _WS_RE.sub("", text)
    if not case_sensitive:
        text = text.casefold()
    return text


_PDF_ESCAPES = {
    b"n": b"\n",
    b"r": b"\r",
    b"t": b"\t",
    b"b": b"\b",
    b"f": b"\f",
    b"(": b"(",
    b")": b")",
    b"\\": b"\\",
}


def unescape_pdf_string(raw: bytes) -> bytes:
    """还原 PDF 字面量字符串中的转义（\\n \\t \\ddd \\( 等）。

    中文水印常以 UTF-16BE 写入，其中的 0x09 / 0x0A 等字节会被转义，
    不还原就无法正确解码匹配。
    """
    if b"\\" not in raw:
        return raw
    out = bytearray()
    i, n = 0, len(raw)
    while i < n:
        c = raw[i : i + 1]
        if c != b"\\" or i + 1 >= n:
            out += c
            i += 1
            continue
        nx = raw[i + 1 : i + 2]
        if nx in _PDF_ESCAPES:
            out += _PDF_ESCAPES[nx]
            i += 2
            continue
        if nx in (b"\r", b"\n"):  # 续行
            i += 1
            if nx == b"\r" and raw[i + 1 : i + 2] == b"\n":
                i += 1
            i += 1
            continue
        if nx.isdigit():  # 八进制
            j = i + 1
            digits = b""
            while j < n and len(digits) < 3 and raw[j : j + 1].isdigit():
                digits += raw[j : j + 1]
                j += 1
            try:
                out.append(int(digits, 8) & 0xFF)
            except Exception:
                pass
            i = j
            continue
        out += nx
        i += 2
    return bytes(out)


def _hex_to_uni(hexstr: str) -> str:
    try:
        if len(hexstr) % 4:
            hexstr = hexstr.zfill(((len(hexstr) + 3) // 4) * 4)
        return bytes.fromhex(hexstr).decode("utf-16-be", "ignore")
    except Exception:
        return ""


def _parse_cmap(data: bytes) -> dict:
    m = {}
    if not data:
        return m
    txt = data.decode("latin-1", "ignore")
    for blk in re.findall(r"beginbfchar(.*?)endbfchar", txt, re.S):
        for src, dst in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", blk):
            m[int(src, 16)] = _hex_to_uni(dst)
    for blk in re.findall(r"beginbfrange(.*?)endbfrange", txt, re.S):
        for lo, hi, dst in re.findall(
            r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", blk
        ):
            lo_i, hi_i, base = int(lo, 16), int(hi, 16), int(dst, 16)
            for k in range(lo_i, hi_i + 1):
                m[k] = chr(base + (k - lo_i))
        for lo, hi, arr in re.findall(
            r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*\[(.*?)\]", blk, re.S
        ):
            items = re.findall(r"<([0-9A-Fa-f]+)>", arr)
            lo_i, hi_i = int(lo, 16), int(hi, 16)
            for idx, k in enumerate(range(lo_i, hi_i + 1)):
                if idx < len(items):
                    m[k] = _hex_to_uni(items[idx])
    return m


def _decode_with_map(raw: bytes, cmap: dict) -> str:
    if not cmap or not raw:
        return ""
    for step in (2, 1):
        if len(raw) % step:
            continue
        codes = [int.from_bytes(raw[i : i + step], "big") for i in range(0, len(raw), step)]
        if all(c in cmap for c in codes):
            return "".join(cmap[c] for c in codes)
    return ""


def _decode_candidates(raw: bytes, cmap: dict) -> list:
    """一个字符串字节序列的多种可能解码，只要任一命中即视为水印。"""
    out = []
    if not raw:
        return out
    mapped = _decode_with_map(raw, cmap)
    if mapped:
        out.append(mapped)
    for enc in ("utf-16-be", "utf-8", "gbk", "big5", "latin-1"):
        try:
            out.append(raw.decode(enc))
        except Exception:
            pass
    return out


def text_matches(raw: bytes, cmap: dict, wm_norm: str, case_sensitive: bool) -> bool:
    for cand in _decode_candidates(raw, cmap):
        if wm_norm and wm_norm in normalize(cand, case_sensitive):
            return True
    return False


# --------------------------------------------------------------------------
# PDF 内容流词法分析
# --------------------------------------------------------------------------

_WS_BYTES = b" \t\r\n\x0c\x00"
_DELIM_BYTES = b"()<>[]{}/%"


def _is_regular(ch: bytes) -> bool:
    return ch not in _WS_BYTES and ch not in _DELIM_BYTES


def _skip_string(data: bytes, start: int) -> int:
    """start 指向 '('，返回字符串结束后的下标。"""
    i = start + 1
    depth = 1
    n = len(data)
    while i < n and depth:
        c = data[i : i + 1]
        if c == b"\\":
            i += 2
            continue
        if c == b"(":
            depth += 1
        elif c == b")":
            depth -= 1
        i += 1
    return i


def tokenize(data: bytes) -> list:
    """把内容流切分为 token：[kind, start, end, value]，kind ∈
    str / name / num / op / arr_open / arr_close / dict / inline。"""
    toks = []
    i, n = 0, len(data)
    while i < n:
        c = data[i : i + 1]
        if c in _WS_BYTES:
            i += 1
            continue
        if c == b"%":
            j = data.find(b"\n", i)
            i = n if j < 0 else j + 1
            continue
        if c == b"(":
            j = _skip_string(data, i)
            toks.append(["str", i, j, unescape_pdf_string(data[i + 1 : j - 1])])
            i = j
            continue
        if data[i : i + 2] == b"<<":
            j = i + 2
            depth = 1
            while j < n and depth:
                if data[j : j + 1] == b"(":
                    j = _skip_string(data, j)
                    continue
                if data[j : j + 2] == b"<<":
                    depth += 1
                    j += 2
                    continue
                if data[j : j + 2] == b">>":
                    depth -= 1
                    j += 2
                    continue
                j += 1
            toks.append(["dict", i, j, None])
            i = j
            continue
        if c == b"<":
            j = data.find(b">", i)
            j = n if j < 0 else j + 1
            hexs = re.sub(rb"[^0-9A-Fa-f]", b"", data[i + 1 : j - 1])
            if len(hexs) % 2:
                hexs += b"0"
            try:
                val = bytes.fromhex(hexs.decode("ascii"))
            except Exception:
                val = b""
            toks.append(["str", i, j, val])
            i = j
            continue
        if c == b"[":
            toks.append(["arr_open", i, i + 1, None])
            i += 1
            continue
        if c == b"]":
            toks.append(["arr_close", i, i + 1, None])
            i += 1
            continue
        if c == b"/":
            j = i + 1
            while j < n and _is_regular(data[j : j + 1]):
                j += 1
            toks.append(["name", i, j, data[i + 1 : j].decode("latin-1")])
            i = j
            continue
        if c in b"+-." or c.isdigit():
            j = i
            while j < n and (data[j : j + 1] in b"+-." or data[j : j + 1].isdigit()):
                j += 1
            toks.append(["num", i, j, None])
            i = j
            continue
        if c in b"'\"":
            toks.append(["op", i, i + 1, c.decode("latin-1")])
            i += 1
            continue
        j = i
        while j < n and _is_regular(data[j : j + 1]):
            j += 1
        if j == i:  # 孤立的分界符（如不成对的 ) } >）：跳过，防止死循环
            i += 1
            continue
        word = data[i:j].decode("latin-1")
        if word == "BI":  # 内联图像，整块跳过
            k = data.find(b"ID", j)
            if k >= 0:
                p = k + 2
                if data[p : p + 1] == b"\r":
                    p += 1
                if data[p : p + 1] == b"\n":
                    p += 1
                e = data.find(b"EI", p)
                e = n if e < 0 else e + 2
                toks.append(["inline", i, e, None])
                i = e
                continue
        toks.append(["op", i, j, word])
        i = j
    return toks


# --------------------------------------------------------------------------
# 操作符级水印摘除
# --------------------------------------------------------------------------

_SHOW_OPS = {"Tj", "TJ", "'", '"'}
_TEXT_STATE_OPS = {"Tf", "Tc", "Tw", "Tz", "Ts", "Tr", "TL"}


def _collect_text_plan(data: bytes, cmap_of: dict, wm_norm: str, case_sensitive: bool):
    """扫描一条内容流，返回 (待删除字节区间, 命中数量, 未命中数量)。"""
    toks = tokenize(data)
    remove = []  # 待删除的字节区间
    matched = 0
    unmatched = 0

    def cmap_for(name):
        return cmap_of.get(name, {})

    def hit(raw):
        return text_matches(raw, cmap_for(cur_font), wm_norm, case_sensitive)

    font_stack = []
    cur_font = None
    operands = []  # [(token_index, token)]
    bt_items = []  # [(token_index, 拼接后的文本)]
    in_bt = False

    i = 0
    n = len(toks)
    while i < n:
        kind, s, e, val = toks[i]
        if kind == "op":
            word = val
            if word == "BT":
                in_bt = True
                bt_items = []
            elif word == "ET":
                # BT..ET 内所有文本拼接后再判断一次（应对水印被拆成多个 Tj 的情况）
                if in_bt and bt_items:
                    joined = "".join(t for _, t in bt_items)
                    if wm_norm and wm_norm in normalize(joined, case_sensitive):
                        for idx, _t in bt_items:
                            remove.append((toks[idx][1], toks[idx][2]))
                        matched += len(bt_items)
                in_bt = False
                bt_items = []
            elif word == "Tf":
                names = [t for idx, t in operands if t[0] == "name"]
                if names:
                    cur_font = names[-1][3]
            elif word == "q":
                font_stack.append(cur_font)
            elif word == "Q":
                cur_font = font_stack.pop() if font_stack else cur_font
            elif word in ("Tj", "'", '"'):
                strs = [(idx, t) for idx, t in operands if t[0] == "str"]
                if strs:
                    idx, tok = strs[-1]
                    if hit(tok[3]):
                        remove.append((tok[1], tok[2]))
                        remove.append((s, e))
                        matched += 1
                    else:
                        unmatched += 1
                        if in_bt:
                            bt_items.append((idx, "".join(_decode_candidates(tok[3], cmap_for(cur_font)))))
            elif word == "TJ":
                arrs = [(idx, t) for idx, t in operands if t[0] == "arr_open"]
                if arrs:
                    a_idx = arrs[-1][0]
                    depth = 0
                    b_idx = a_idx
                    for k in range(a_idx, n):
                        if toks[k][0] == "arr_open":
                            depth += 1
                        elif toks[k][0] == "arr_close":
                            depth -= 1
                            if depth == 0:
                                b_idx = k
                                break
                    inner = [(k, toks[k]) for k in range(a_idx + 1, b_idx) if toks[k][0] == "str"]
                    hit_flags = [hit(t[3]) for _k, t in inner]
                    for (k, tok), flag in zip(inner, hit_flags):
                        if flag:
                            remove.append((tok[1], tok[2]))
                            matched += 1
                        else:
                            unmatched += 1
                            if in_bt:
                                bt_items.append(
                                    (k, "".join(_decode_candidates(tok[3], cmap_for(cur_font))))
                                )
                    if hit_flags and all(hit_flags):
                        remove.append((toks[a_idx][1], toks[a_idx][2]))
                        remove.append((toks[b_idx][1], toks[b_idx][2]))
                        remove.append((s, e))
            operands = []
        else:
            operands.append((i, toks[i]))
            if len(operands) > 8:
                operands.pop(0)
        i += 1

    # 合并区间
    merged = []
    for a, b in sorted(remove):
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return merged, matched, unmatched


def _strip_ranges(data: bytes, ranges) -> bytes:
    if not ranges:
        return data
    out = bytearray()
    pos = 0
    for a, b in ranges:
        out += data[pos:a]
        pos = max(pos, b)
    out += data[pos:]
    # 清理连续空行 / 孤立空白，保持可读性
    return re.sub(rb"[ \t]*\n[ \t]*(?=\n)", b"\n", bytes(out))


# --------------------------------------------------------------------------
# 文档级处理
# --------------------------------------------------------------------------


class RemoveResult:
    def __init__(self):
        self.source = ""
        self.output = ""
        self.pages = 0
        self.pages_hit = 0
        self.streams_removed = 0
        self.ops_removed = 0
        self.annots_removed = 0
        self.xobjects_cleaned = 0
        self.residual = 0
        self.log = []

    def add(self, msg):
        self.log.append(msg)

    def as_text(self):
        return "\n".join(self.log)


def _page_font_cmaps(doc, page):
    cmaps = {}
    try:
        fonts = page.get_fonts()
    except Exception:
        fonts = []
    for item in fonts:
        try:
            xref, _ext, _typ, _base, name, _enc = item[:6]
            name = str(name).lstrip("/")
            key = doc.xref_get_key(int(xref), "ToUnicode")
            cmap = {}
            if key and key[0] == "xref":
                tx = int(str(key[1]).split()[0])
                cmap = _parse_cmap(doc.xref_stream(tx) or b"")
            cmaps[name] = cmap
        except Exception:
            continue
    return cmaps


def _page_content_xrefs(doc, page):
    try:
        xrefs = list(page.get_contents())
    except Exception:
        xrefs = []
    if xrefs:
        return xrefs
    val = doc.xref_get_key(page.xref, "Contents")[1]
    m = re.match(r"^\s*(\d+)\s+0\s+R\s*$", val or "")
    return [int(m.group(1))] if m else []


def _raw_hit(data: bytes, wm: str) -> bool:
    """直接在原始字节里找水印（覆盖 UTF-16BE / UTF-8 / GBK 直写的情况）。"""
    if not data or not wm:
        return False
    variants = {wm, _WS_RE.sub("", wm)}
    for text in variants:
        if not text:
            continue
        for enc in ("utf-16-be", "utf-16-le", "utf-8", "gbk"):
            try:
                if text.encode(enc) in data:
                    return True
            except Exception:
                pass
    return False


def _clean_annots(doc, page, wm_norm, case_sensitive, result, pno):
    try:
        annots = list(page.annots())
    except Exception:
        return
    for annot in annots:
        try:
            if annot.type[0] in (5, 6):  # Widget / Sig：不动表单与签名
                continue
            txt = annot.get_text() or ""
            info = annot.info or {}
            cand = " ".join([txt, str(info.get("content") or ""), str(info.get("title") or "")])
            if wm_norm and wm_norm in normalize(cand, case_sensitive):
                page.delete_annot(annot)
                result.annots_removed += 1
                result.add(f"  第 {pno + 1} 页：删除水印注解（{annot.type[1]}）")
        except Exception:
            continue


def _process_xobjects(doc, page, cmaps, wm, wm_norm, case_sensitive, result, pno):
    try:
        xobjects = page.get_xobjects()
    except Exception:
        return
    for item in xobjects:
        try:
            xref = int(item[0])
            obj = doc.xref_object(xref)
            if "/Form" not in obj:
                continue
            data = doc.xref_stream(xref) or b""
            if not data or not _raw_hit(data, wm):
                if not wm_norm:
                    continue
            ranges, matched, _un = _collect_text_plan(data, cmaps, wm_norm, case_sensitive)
            if ranges:
                doc.update_stream(xref, _strip_ranges(data, ranges))
                result.xobjects_cleaned += 1
                result.ops_removed += len(ranges)
                result.add(f"  第 {pno + 1} 页：清理表单 XObject(xref={xref}) 内 {len(ranges)} 处水印文本")
        except Exception:
            continue


def _process_page(doc, page, pno, wm, wm_norm, case_sensitive, result):
    cmaps = _page_font_cmaps(doc, page)
    xrefs = _page_content_xrefs(doc, page)

    _clean_annots(doc, page, wm_norm, case_sensitive, result, pno)

    keep, dropped = [], []
    for x in xrefs:
        data = doc.xref_stream(x) or b""
        if not data:
            keep.append(x)
            continue
        ranges, matched, unmatched = _collect_text_plan(data, cmaps, wm_norm, case_sensitive)
        raw_hit = _raw_hit(data, wm)
        if matched and unmatched == 0:
            # 整条流都只画水印 —— 直接摘除
            dropped.append((x, data))
            continue
        if ranges:
            new_data = _strip_ranges(data, ranges)
            if new_data != data:
                doc.update_stream(x, new_data)
                result.ops_removed += len(ranges)
                result.add(
                    f"  第 {pno + 1} 页：删除 {len(ranges)} 处水印文本（与正文混排，原位摘除）"
                )
        elif raw_hit:
            # 无法结构化解析但原始字节命中，保守处理：整流摘除（仅当流很小）
            if len(data) < 4096 and b"Tj" in data:
                dropped.append((x, data))
                continue
        keep.append(x)

    if dropped:
        remaining = b"".join((doc.xref_stream(x) or b"") for x in keep)
        used_font = set(re.findall(rb"/([A-Za-z0-9_+\-]+)\s+[\d.]+\s+Tf", remaining))
        used_gs = set(re.findall(rb"/([A-Za-z0-9_+\-]+)\s+gs", remaining))
        for x, data in dropped:
            for nm in set(re.findall(rb"/([A-Za-z0-9_+\-]+)\s+[\d.]+\s+Tf", data)):
                if nm.decode("latin-1") not in {u.decode("latin-1") for u in used_font}:
                    try:
                        doc.xref_set_key(page.xref, f"Resources/Font/{nm.decode('latin-1')}", "null")
                    except Exception:
                        pass
            for nm in set(re.findall(rb"/([A-Za-z0-9_+\-]+)\s+gs", data)):
                if nm.decode("latin-1") not in {u.decode("latin-1") for u in used_gs}:
                    try:
                        doc.xref_set_key(
                            page.xref, f"Resources/ExtGState/{nm.decode('latin-1')}", "null"
                        )
                    except Exception:
                        pass
            result.streams_removed += 1
            result.add(f"  第 {pno + 1} 页：整条移除水印内容流 xref={x}")
        if len(keep) == 1:
            new_contents = f"{keep[0]} 0 R"
        elif keep:
            new_contents = "[" + " ".join(f"{x} 0 R" for x in keep) + "]"
        else:
            new_contents = "[ ]"
        try:
            doc.xref_set_key(page.xref, "Contents", new_contents)
        except Exception as e:
            result.add(f"  第 {pno + 1} 页：写入 /Contents 失败：{e}")

    _process_xobjects(doc, page, cmaps, wm, wm_norm, case_sensitive, result, pno)


def _count_hits(doc, wm_norm, case_sensitive):
    hits = 0
    for pno in range(doc.page_count):
        try:
            txt = doc[pno].get_text("text") or ""
        except Exception:
            txt = ""
        if wm_norm and wm_norm in normalize(txt, case_sensitive):
            hits += 1
    return hits


def _clean_metadata(doc, wm, result):
    try:
        meta = doc.metadata or {}
        changed = False
        new_meta = dict(meta)
        for k, v in meta.items():
            if isinstance(v, str) and wm and wm in v:
                new_meta[k] = v.replace(wm, "")
                changed = True
        if changed:
            doc.set_metadata(new_meta)
            result.add("  已清理文档属性中的水印文字")
    except Exception:
        pass
    try:
        xmp = doc.get_xml_metadata() or ""
        if wm and wm in xmp:
            doc.set_xml_metadata(xmp.replace(wm, ""))
            result.add("  已清理 XMP 元数据中的水印文字")
    except Exception:
        pass


# --------------------------------------------------------------------------
# 水印自动识别
# --------------------------------------------------------------------------


def _page_extgstate_alphas(doc, page) -> dict:
    """读取页面 ExtGState 资源中的不透明度 ca/CA。"""
    alphas = {}
    try:
        typ, val = doc.xref_get_key(page.xref, "Resources")
        res_xref = None
        if typ == "xref":
            res_xref = int(str(val).split()[0])
        elif typ == "dict":
            raw = val
        else:
            raw = ""
        gs_dict = ""
        if res_xref:
            typ2, val2 = doc.xref_get_key(res_xref, "ExtGState")
            if typ2 == "xref":
                gs_dict = doc.xref_object(int(str(val2).split()[0]))
            elif typ2 == "dict":
                gs_dict = val2
        else:
            m = re.search(r"/ExtGState\s*<<(.*?)>>", raw or "", re.S)
            if m:
                gs_dict = "<<" + m.group(1) + ">>"
        for name, ref in re.findall(r"/([A-Za-z0-9_+\-]+)\s+(\d+)\s+0\s+R", gs_dict):
            try:
                obj = doc.xref_object(int(ref))
            except Exception:
                continue
            m = re.search(r"/(?:ca|CA)\s+([0-9.]+)", obj)
            if m:
                try:
                    alphas[name] = float(m.group(1))
                except Exception:
                    pass
    except Exception:
        pass
    return alphas


def _scan_text_attributes(data: bytes, cmaps: dict, gs_alpha: dict) -> dict:
    """扫描一条内容流，收集每段文本的绘制属性：透明度 / 是否旋转 / 绘制顺序。"""
    toks = tokenize(data)
    attrs = {}
    cur_font = None
    cur_gs = None
    cur_rot = False
    order = 0
    stack = []
    operands = []

    def note(raw):
        if not raw:
            return
        alpha = gs_alpha.get(cur_gs, 1.0) if cur_gs else 1.0
        cand = _best_decode(raw, cmaps.get(cur_font, {}))
        key = normalize(cand, True)
        if len(key) < 2 or _is_mojibake(cand):
            return
        rec = attrs.get(key)
        if rec is None:
            rec = {"alpha": 1.0, "rotated": False, "order": order, "n": 0, "sample": ""}
            attrs[key] = rec
        rec["alpha"] = min(rec["alpha"], alpha)
        rec["rotated"] = rec["rotated"] or cur_rot
        rec["order"] = min(rec["order"], order)
        rec["n"] += 1
        if len(cand) > len(rec["sample"]):
            rec["sample"] = cand

    for kind, s, e, val in toks:
        if kind == "op":
            w = val
            if w == "Tf":
                names = [t for t in operands if t[0] == "name"]
                if names:
                    cur_font = names[-1][3]
            elif w == "gs":
                names = [t for t in operands if t[0] == "name"]
                if names:
                    cur_gs = names[-1][3]
            elif w == "q":
                stack.append((cur_gs, cur_rot, cur_font))
            elif w == "Q":
                if stack:
                    cur_gs, cur_rot, cur_font = stack.pop()
            elif w == "Tm":
                nums = [t for t in operands if t[0] == "num"]
                if len(nums) >= 6:
                    try:
                        b = float(data[nums[-5][1] : nums[-5][2]])
                        c = float(data[nums[-4][1] : nums[-4][2]])
                        if abs(b) > 1e-3 or abs(c) > 1e-3:
                            cur_rot = True
                    except Exception:
                        pass
            elif w in ("Tj", "'", '"'):
                strs = [t for t in operands if t[0] == "str"]
                if strs:
                    note(strs[-1][3])
                    order += 1
            elif w == "TJ":
                for t in operands:
                    if t[0] == "str":
                        note(t[3])
                order += 1
            operands = []
        else:
            operands.append((kind, s, e, val))
    return attrs


def _is_mojibake(text: str) -> bool:
    """粗略判断是否为乱码（大量私用区 / 控制字符 / 替换符）。"""
    if not text:
        return True
    bad = 0
    for ch in text:
        o = ord(ch)
        if o in (0xFFFD,) or 0xE000 <= o <= 0xF8FF or (o < 0x20 and ch not in "\t\r\n") or 0x7F <= o <= 0x9F:
            bad += 1
    return bad > max(1, len(text) * 0.15)


def _best_decode(raw: bytes, cmap: dict) -> str:
    """给出最可信的一种解码，用于聚合统计（避免乱码候选污染结果）。"""
    if not raw:
        return ""
    if cmap:
        mapped = _decode_with_map(raw, cmap)
        if mapped and not _is_mojibake(mapped):
            return mapped
    for enc in ("utf-16-be", "gbk", "utf-8"):
        try:
            s = raw.decode(enc)
        except Exception:
            continue
        if not _is_mojibake(s):
            return s
    return raw.decode("latin-1", "ignore")


def _pick_display_text(samples: dict) -> str:
    """从出现过的原文样本中挑一个最具代表性的展示文本。"""
    if not samples:
        return ""
    return max(samples.items(), key=lambda kv: kv[1])[0]


def analyze_watermarks(src: str, max_pages: int = 6, progress=None) -> list:
    """自动识别可能的水印文字，返回按置信度排序的候选列表。

    判据：跨页重复出现、每页多次重复、斜向排版、半透明、位于背景层。
    文本来源双保险：页面渲染结果 + 内容流字符串解码（应对扫描件 / 特殊字体）。
    """
    doc = fitz.open(src)
    total = doc.page_count
    scan_n = min(total, max_pages) if max_pages and max_pages > 0 else total
    stats = {}
    try:
        for pno in range(scan_n):
            if progress:
                try:
                    progress(pno, scan_n, "分析第 %d 页" % (pno + 1))
                except Exception:
                    pass
            page = doc[pno]
            cmaps = _page_font_cmaps(doc, page)
            alphas = _page_extgstate_alphas(doc, page)

            # 1) 页面渲染结果（文本最准确）
            try:
                rendered = page.get_text("dict")
            except Exception:
                rendered = {"blocks": []}
            for blk in rendered.get("blocks", []):
                if blk.get("type") != 0:
                    continue
                for line in blk.get("lines", []):
                    for span in line.get("spans", []):
                        text = (span.get("text") or "").strip()
                        if len(text) < 2:
                            continue
                        key = normalize(text, True)
                        rec = stats.setdefault(
                            key,
                            {
                                "samples": {},
                                "pages": set(),
                                "count": 0,
                                "sizes": {},
                                "rot": False,
                                "orders": [],
                            },
                        )
                        rec["samples"][text] = rec["samples"].get(text, 0) + 1
                        rec["pages"].add(pno)
                        rec["count"] += 1
                        sz = round(float(span.get("size") or 0), 1)
                        rec["sizes"][sz] = rec["sizes"].get(sz, 0) + 1
                        d = span.get("dir")
                        if d and abs(d[1]) > 0.05:
                            rec["rot"] = True

            # 2) 内容流：透明度 / 旋转 / 绘制顺序 / 兜底文本
            merged = {}
            for x in _page_content_xrefs(doc, page):
                data = doc.xref_stream(x) or b""
                if not data:
                    continue
                for k, v in _scan_text_attributes(data, cmaps, alphas).items():
                    cur = merged.get(k)
                    if cur is None:
                        merged[k] = dict(v)
                    else:
                        cur["alpha"] = min(cur["alpha"], v["alpha"])
                        cur["rotated"] = cur["rotated"] or v["rotated"]
                        cur["order"] = min(cur["order"], v["order"])
                        cur["n"] += v["n"]
                        if len(v.get("sample", "")) > len(cur.get("sample", "")):
                            cur["sample"] = v["sample"]
            total_texts = sum(v["n"] for v in merged.values()) or 1
            for key, a in merged.items():
                rec = stats.get(key)
                if rec is None:
                    # 渲染层取不到文本时用解码结果兜底
                    if len(a.get("sample", "")) < 2 or _is_mojibake(a.get("sample", "")):
                        continue
                    rec = {
                        "samples": {a["sample"]: 1},
                        "pages": {pno},
                        "count": a["n"],
                        "sizes": {},
                        "rot": False,
                        "orders": [],
                    }
                    stats[key] = rec
                if pno not in rec["pages"]:
                    rec["pages"].add(pno)
                rec["alpha"] = min(rec.get("alpha", 1.0), a["alpha"])
                rec["rot"] = rec["rot"] or a["rotated"]
                rec["orders"].append(a["order"] / float(total_texts))

        cands = []
        page_need = max(2, int(scan_n * 0.6)) if scan_n >= 2 else 1
        for key, rec in stats.items():
            text = _pick_display_text(rec["samples"])
            if len(key) < 4 or len(text) < 2:
                continue
            if _is_mojibake(text):
                continue
            if sum(1 for ch in key if ch.isalpha()) < 2:  # 页码等纯数字噪声
                continue
            pages = len(rec["pages"])
            count = rec["count"]
            per_page = count / max(pages, 1)
            alpha = rec.get("alpha", 1.0)
            rotated = bool(rec.get("rot"))
            orders = rec.get("orders") or []
            bg = bool(orders) and (sum(orders) / len(orders)) < 0.25

            score = 0
            feats = []
            if count >= 2:
                score += 15
            if pages >= 2:
                score += 15
                feats.append("%d 页重复" % pages)
            if pages >= page_need and count >= 2:
                score += 15
                feats.append("几乎每页都有")
            if per_page >= 2:
                score += 25
                feats.append("每页约 %d 次" % round(per_page))
            if rotated:
                score += 20
                feats.append("斜向排版")
            if alpha < 1.0:
                score += 20
                feats.append("半透明 %g" % alpha)
                if alpha <= 0.3:
                    score += 5
            if bg:
                score += 10
                feats.append("背景层")
            if len(key) >= 6:
                score += 5
            if pages <= 1 and count <= 1:
                score -= 100

            if score < 50:
                continue
            cands.append(
                {
                    "text": _pick_display_text(rec["samples"]),
                    "key": key,
                    "score": score,
                    "pages": pages,
                    "count": count,
                    "per_page": round(per_page, 1),
                    "alpha": alpha,
                    "rotated": rotated,
                    "background": bg,
                    "features": "、".join(feats) or "重复文本",
                    "size": max(rec["sizes"], key=rec["sizes"].get) if rec["sizes"] else 0,
                }
            )
        seen = set()
        uniq = []
        for c in sorted(cands, key=lambda c: (-c["score"], -c["pages"], -c["count"])):
            k = c["key"][:24]
            if k in seen:
                continue
            seen.add(k)
            uniq.append(c)
        return uniq[:12]
    finally:
        doc.close()


def scan_document(src: str, wm: str, case_sensitive: bool = False) -> dict:
    """只检测，不修改：返回每页命中情况。"""
    wm_norm = normalize(wm, case_sensitive)
    doc = fitz.open(src)
    info = {"pages": doc.page_count, "hit_pages": [], "total_ops": 0}
    try:
        for pno in range(doc.page_count):
            page = doc[pno]
            cmaps = _page_font_cmaps(doc, page)
            cnt = 0
            for x in _page_content_xrefs(doc, page):
                data = doc.xref_stream(x) or b""
                _r, m, _u = _collect_text_plan(data, cmaps, wm_norm, case_sensitive)
                cnt += m
            try:
                txt = page.get_text("text") or ""
            except Exception:
                txt = ""
            raw_all = b"".join((doc.xref_stream(x) or b"") for x in _page_content_xrefs(doc, page))
            if cnt == 0 and _raw_hit(raw_all, wm):
                cnt = max(cnt, 1)
            if cnt or (wm_norm and wm_norm in normalize(txt, case_sensitive)):
                info["hit_pages"].append(pno + 1)
                info["total_ops"] += max(cnt, 1)
    finally:
        doc.close()
    return info


def remove_watermark(
    src: str,
    dst: str,
    wm: str,
    case_sensitive: bool = False,
    clean_metadata: bool = False,
    overwrite: bool = False,
    progress=None,
) -> RemoveResult:
    """执行去水印，返回 RemoveResult。"""
    result = RemoveResult()
    result.source = src
    result.output = dst
    wm_norm = normalize(wm, case_sensitive)

    if not wm_norm:
        raise ValueError("水印文字不能为空")

    def tick(pno, total, msg):
        if progress:
            try:
                progress(pno, total, msg)
            except Exception:
                pass

    doc = fitz.open(src)
    try:
        if doc.needs_pass and not doc.authenticate(""):
            raise RuntimeError("文档已加密，需要口令才能处理")
        result.pages = doc.page_count
        before_hits = _count_hits(doc, wm_norm, case_sensitive)
        result.pages_hit = before_hits
        result.add(f"处理前：{result.pages} 页，命中水印的页面 {before_hits} 页")

        for pno in range(doc.page_count):
            tick(pno, doc.page_count, f"正在处理第 {pno + 1} 页")
            try:
                _process_page(doc, doc[pno], pno, wm, wm_norm, case_sensitive, result)
            except Exception as e:
                result.add(f"  第 {pno + 1} 页处理异常：{e}")

        if clean_metadata:
            _clean_metadata(doc, wm, result)

        tmp_dir = None
        out_path = dst
        if overwrite and os.path.abspath(src) == os.path.abspath(dst):
            tmp_dir = tempfile.mkdtemp(prefix="pdfwm_")
            out_path = os.path.join(tmp_dir, os.path.basename(src))

        doc.save(out_path, garbage=4, deflate=True, clean=True)

        if tmp_dir:
            shutil.move(out_path, dst)
            shutil.rmtree(tmp_dir, ignore_errors=True)
    finally:
        doc.close()

    # 复检
    chk = fitz.open(dst)
    try:
        result.residual = _count_hits(chk, wm_norm, case_sensitive)
    finally:
        chk.close()

    result.add(
        f"处理完成：移除内容流 {result.streams_removed} 条 / 文本片段 {result.ops_removed} 处 / "
        f"注解 {result.annots_removed} 个 / 表单对象 {result.xobjects_cleaned} 个"
    )
    result.add(f"复检：仍有水印文字的页面 {result.residual} 页" if result.residual else "复检：水印已全部清除")
    return result
