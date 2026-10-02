"""Developer-only, reproducible print font derivative. No runtime dependency.

Give distinct Unicode characters distinct glyph IDs so Chromium's PDF exporter
cannot reverse-map a Han character to a visually identical radical alias.
Outlines, advance widths and Unicode coverage are retained. OFL reserved names
are replaced; the original copyright and license records remain intact.
"""
from __future__ import annotations

import argparse
from copy import copy
import hashlib
from pathlib import Path

UPSTREAM_SHA256 = "3754ea669c530e2473354f8f6d9f79680a44d7e26ec7d00eeabee4a7e0753c5d"


def build(source: Path, target: Path) -> dict:
    from fontTools.ttLib import TTFont
    from fontTools.pens.recordingPen import RecordingPen

    if hashlib.sha256(source.read_bytes()).hexdigest() != UPSTREAM_SHA256:
        raise ValueError("Expected unmodified Source Han Serif CN Regular 2.003R")
    if target.exists():
        raise FileExistsError(target)
    font = TTFont(source, recalcTimestamp=False)
    top = font["CFF "].cff.topDictIndex[0]
    order = font.getGlyphOrder()
    cmap = font.getBestCmap()
    groups: dict[str, list[int]] = {}
    for cp, glyph in cmap.items():
        groups.setdefault(glyph, []).append(cp)
    used_cids = {int(name[3:]) for name in order if name.startswith("cid")}
    next_cid = 1
    changes = {}
    for glyph, codepoints in groups.items():
        # Keep ordinary Han on its original ID, including its OpenType lookups.
        keeper = next((cp for cp in codepoints if 0x4E00 <= cp <= 0x9FFF), codepoints[0])
        for cp in codepoints:
            if cp == keeper:
                continue
            while next_cid in used_cids:
                next_cid += 1
            if next_cid > 65535:
                raise ValueError("CFF CID space exhausted")
            name = f"cid{next_cid:05d}"
            used_cids.add(next_cid)
            charstring, selector = top.CharStrings.getItemAndSelector(glyph)
            top.CharStrings.charStrings[name] = len(top.CharStrings.charStringsIndex)
            top.CharStrings.charStringsIndex.append(copy(charstring))
            top.FDSelect.append(selector)
            order.append(name)
            for tag in ("hmtx", "vmtx"):
                font[tag].metrics[name] = font[tag].metrics[glyph]
            if glyph in font["VORG"].VOriginRecords:
                font["VORG"].VOriginRecords[name] = font["VORG"].VOriginRecords[glyph]
            changes[cp] = name
    for table in font["cmap"].tables:
        if table.isUnicode() and hasattr(table, "cmap"):
            for cp, name in changes.items():
                if cp in table.cmap:
                    table.cmap[cp] = name
    top.charset = order
    top.CIDCount = max(top.CIDCount, max(used_cids) + 1)
    font.setGlyphOrder(order)
    font["maxp"].numGlyphs = len(order)
    names = {1: "ZQ Book Song", 2: "Regular", 3: "ZQBookSong-Regular-2.003.1",
             4: "ZQ Book Song Regular", 6: "ZQBookSong-Regular",
             16: "ZQ Book Song", 17: "Regular"}
    for record in font["name"].names:
        if record.nameID in names:
            record.string = names[record.nameID].encode(record.getEncoding())
    font["CFF "].cff.fontNames = ["ZQBookSong-Regular"]
    top.FullName, top.FamilyName = "ZQ Book Song Regular", "ZQ Book Song"
    for fd in top.FDArray:
        if hasattr(fd, "FontName"):
            fd.FontName = "ZQBookSong-" + fd.FontName.rsplit("-", 1)[-1]
    if "DSIG" in font:
        del font["DSIG"]  # Modification invalidates any upstream signature.
    target.parent.mkdir(parents=True, exist_ok=True)
    font.save(target)
    verified = TTFont(target)
    assert set(verified.getBestCmap()) == set(cmap), "Unicode coverage changed"
    assert len(set(verified.getBestCmap().values())) == len(cmap), "Aliases remain"
    old_set, new_set = font.getGlyphSet(), verified.getGlyphSet()
    for cp, name in changes.items():
        before, after = RecordingPen(), RecordingPen()
        old_set[cmap[cp]].draw(before)
        new_set[name].draw(after)
        assert before.value == after.value, "Glyph outline changed"
        for tag in ("hmtx", "vmtx"):
            assert font[tag].metrics[cmap[cp]] == verified[tag].metrics[name]
    font.close()
    verified.close()
    return {"duplicate_glyphs": len(changes), "bytes": target.stat().st_size,
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}


if __name__ == "__main__":
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.target)))
