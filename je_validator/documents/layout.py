"""
Arranges a page's text by its layout, so that blocks side by side (vendor
top-left, invoice details top-right) are read one after the other instead of
line by line across the page, and values under a heading keep that heading.

Input: the page's text lines with their boxes (from OCR, or from a PDF's text
layer) and, optionally, the zones a layout model found (box, label, score).

  1. Zones are cleaned: a zone holding more confident zones is a coarse
     duplicate and goes; of two zones on the same spot, the more confident stays.
  2. Each line joins the smallest zone holding its centre; lines in no zone
     are arranged on their own.
  3. Reading order is XY-cut: split at horizontal gaps into bands (top to
     bottom), split each band at vertical gaps into columns (left to right),
     and so on. It needs no model, so it also orders pages without zones.
  4. In each zone, lines on the same height form rows, with cells joined by
     " | ". When a zone's first row is headings (no amounts) and later rows sit
     under them, each value is written with its heading - "Date: 28/10/2026",
     "Amount: 1,200.00" - so what is above a value travels with it.

Pure geometry: it doesn't depend on any OCR engine.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from je_validator.validation.amounts import has_amount

Box = Tuple[float, float, float, float]  # x0, y0, x1, y1 in page pixels


@dataclass
class Line:
    text: str
    box: Box
    score: float = 1.0  # recognition confidence; 1.0 for a PDF's text layer


@dataclass
class Zone:
    label: str
    box: Box
    score: float
    lines: List[Line] = field(default_factory=list)
    text: Optional[List[str]] = None  # rows read another way (PaddleOCR-VL), used instead of the lines
    note: Optional[str] = None  # how the zone was read, shown in the viewer


def clean_zones(zones: Sequence[Zone], lines: Sequence[Line] = ()) -> List[Zone]:
    """The zones without the layout model's coarse duplicates (see _clean); lines: the page's text."""
    return _clean([Zone(z.label, z.box, z.score, [], z.text, z.note) for z in zones], lines)


def join_zones(zones: Sequence[Zone], lines: Sequence[Line]) -> List[Zone]:
    """The zones cleaned, with the parts of a table the layout model split joined into one zone (see
    _join_split_tables), so that PaddleOCR-VL (OCR_MODE=hybrid) reads the table whole, each item with
    its amount. Without their lines: arrange gives each zone its lines."""
    zones = clean_zones(zones, lines)
    for line in lines:
        home = _home(line, zones) if line.text.strip() else None
        if home is not None:
            home.lines.append(line)
    joined = _join_split_tables([z for z in zones if z.lines]) + [z for z in zones if not z.lines]
    return [Zone(z.label, z.box, z.score, [], z.text, z.note) for z in joined]


def arrange(lines: Sequence[Line], zones: Sequence[Zone] = ()) -> Tuple[str, List[Dict[str, Any]]]:
    """(the page's text, zone by zone in reading order; its zones as {n, label, box, score[, note]} for
    display). A zone with text of its own (read by PaddleOCR-VL) shows that text instead of its lines."""
    zones = clean_zones(zones, lines)
    loose: List[Zone] = []
    for line in lines:
        if not line.text.strip():
            continue
        home = _home(line, zones)
        if home is None:
            loose.append(Zone("other", line.box, 0.0, [line]))
        else:
            home.lines.append(line)
    units = _join_split_tables([z for z in zones if z.lines]) + loose
    ordered = _merge_loose([units[i] for i in _xy_cut([u.box for u in units])])
    blocks, shown = [], []
    for n, zone in enumerate(ordered, 1):
        in_order = [zone.lines[i] for i in _xy_cut([l.box for l in zone.lines])]
        rows = zone.text if zone.text is not None else _format(_pair_amounts(in_order))
        blocks.append(f"[Zone {n} - {zone.label}]\n" + "\n".join(rows))
        box = _bounds([l.box for l in zone.lines]) if zone.label == "other" else zone.box
        shown.append({"n": n, "label": zone.label, "box": [round(v) for v in box], "score": round(zone.score, 2),
                      **({"note": zone.note} if zone.note else {})})
    return "\n".join(blocks), shown


# ---------------------------------------------------------------------------
# Zones
# ---------------------------------------------------------------------------

CONTAINER_MARGIN = 0.1  # zones held by a zone and this nearly as confident can stand for it


def _clean(zones: List[Zone], lines: Sequence[Line] = ()) -> List[Zone]:
    """A zone is coarse - a duplicate of finer zones - when it holds a more confident zone, or two or
    more nearly as confident: a 'table' over the whole page at 0.51 whose blocks were also found at
    0.46-0.49, or a table where only its Description column was also found. The finer zones are kept;
    text the coarse zone held that no kept zone holds becomes zones of its own (one per block, with the
    coarse zone's label): the Qty | Price | Amount columns beside that Description column (joined back
    into one table later), or a block the model found only as part of the whole page. A real table
    (0.91) holding faint cell boxes (0.3) is not coarse. Of zones on the same spot, the larger stays,
    then the more confident."""
    def held(i, a):
        return [b for j, b in enumerate(zones) if j != i and _inside(b.box, a.box)]

    coarse = [i for i, a in enumerate(zones)
              if any(b.score > a.score for b in held(i, a))
              or sum(b.score >= a.score - CONTAINER_MARGIN for b in held(i, a)) >= 2]
    kept: List[Zone] = []
    for z in sorted((z for i, z in enumerate(zones) if i not in coarse), key=lambda z: (-_area(z.box), -z.score)):
        if not any(_inside(z.box, k.box) or _inside(k.box, z.box) for k in kept):
            kept.append(z)
    for i in sorted(coarse, key=lambda i: _area(zones[i].box)):  # inner ones first: they claim their text
        a = zones[i]
        left = [l for l in lines if _holds(a.box, l) and not any(_holds(k.box, l) for k in kept)]
        for block in _blocks(left):
            kept.append(Zone(a.label, _bounds([l.box for l in block]), a.score))
    return kept


def _blocks(lines: List[Line]) -> List[List[Line]]:
    """Lines split into blocks where a vertical gap is wider than one and a half lines."""
    blocks: List[List[Line]] = []
    for line in sorted(lines, key=lambda l: l.box[1]):
        height = line.box[3] - line.box[1]
        if blocks and line.box[1] - max(l.box[3] for l in blocks[-1]) <= 1.5 * height:
            blocks[-1].append(line)
        else:
            blocks.append([line])
    return blocks


def _holds(box: Box, line: Line) -> bool:
    cx, cy = (line.box[0] + line.box[2]) / 2, (line.box[1] + line.box[3]) / 2
    return box[0] <= cx <= box[2] and box[1] <= cy <= box[3]


def _home(line: Line, zones: List[Zone]) -> Optional[Zone]:
    cx, cy = (line.box[0] + line.box[2]) / 2, (line.box[1] + line.box[3]) / 2
    holding = [z for z in zones if z.box[0] <= cx <= z.box[2] and z.box[1] <= cy <= z.box[3]]
    return min(holding, key=lambda z: _area(z.box)) if holding else None


def _join_split_tables(zones: List[Zone]) -> List[Zone]:
    """A layout model may cut a table without borders into side-by-side zones (a Description column,
    then Qty | Price | Amount), which separates each item from its amount. Two zones side by side are
    one table when both start with a heading row, they have the same rows - one for one, at the same
    heights - and there are money amounts (two decimals) under the headings. Blocks that merely sit side by side (Bill to
    beside a contact, an address beside the invoice details, a VAT table beside the totals) differ in
    rows, or have no heading row, or no amounts under it. A table without headings may also be cut into
    its labels and a bare column of their amounts: that column joins the labels beside it."""
    zones = list(zones)
    joined = True
    while joined:
        joined = False
        for a in zones:
            for b in zones:
                if a is not b and _one_table(a, b):
                    zones.remove(a)
                    zones.remove(b)
                    zones.append(Zone("table", _bounds([a.box, b.box]), max(a.score, b.score), a.lines + b.lines))
                    joined = True
                    break
            if joined:
                break
    for right in [z for z in zones if _amounts_only(z)]:
        if not any(z is right for z in zones):
            continue
        lefts = _label_zones(right, [z for z in zones if z is not right])
        if lefts:
            zones = [z for z in zones if not any(z is j for j in [right, *lefts])]
            zones.append(Zone(lefts[0].label, _bounds([z.box for z in [*lefts, right]]),
                              max(z.score for z in [*lefts, right]), [l for z in [*lefts, right] for l in z.lines]))
    return zones


def _bare(line: Line) -> bool:
    return bool(_MONEY.fullmatch(_CURRENCY.sub("", line.text).strip()))


def _amounts_only(zone: Zone) -> bool:
    """A zone that is mostly a bare column of money amounts: the amounts of labels the layout model put in
    another zone (an invoice's items and totals on the left, their amounts on the right). It may hold
    the ends of other rows too ('Account No ...' at the end of the bank details)."""
    return zone.text is None and sum(map(_bare, zone.lines)) >= 2


def _label_zones(right: Zone, others: List[Zone]) -> List[Zone]:
    """The zones holding the labels of right's amounts - usually one, but a layout model may box each
    label on its own - or [] if they aren't there. Each line of right needs a line at its height in the
    nearest zone on its left, and a bare amount needs one without amounts. A block of figures (a VAT
    table) beside the amounts has amounts of its own; a block whose lines don't line up is not the labels."""
    found: List[Zone] = []
    for line in right.lines:
        centre, tolerance = (line.box[1] + line.box[3]) / 2, 0.5 * (line.box[3] - line.box[1])
        beside = [(z, level) for z in others if z.text is None and z.box[2] <= right.box[0] + 5
                  if (level := [l for l in z.lines if abs((l.box[1] + l.box[3]) / 2 - centre) <= tolerance])]
        if not beside:
            return []
        zone, level = max(beside, key=lambda p: p[0].box[2])  # the nearest on the left
        if _bare(line) and any(has_amount(l.text) for l in level):
            return []
        if not any(zone is f for f in found):
            found.append(zone)
    return found


_CURRENCY = re.compile(r"[A-Z]{3}|[£$€¥]")


def _one_table(left: Zone, right: Zone) -> bool:
    if left.text is not None or right.text is not None:  # already read whole, by PaddleOCR-VL
        return False
    if left.box[2] > right.box[0] + 5:  # right must lie to the right of left
        return False
    shared = min(left.box[3], right.box[3]) - max(left.box[1], right.box[1])
    if shared < 0.5 * min(left.box[3] - left.box[1], right.box[3] - right.box[1]):
        return False
    rows = [_rows([z.lines[i] for i in _xy_cut([l.box for l in z.lines])]) for z in (left, right)]
    if len(rows[0]) != len(rows[1]) or len(rows[0]) < 2:
        return False
    for r, s in zip(*rows):
        centre = lambda row: sum(l.box[1] + l.box[3] for l in row) / (2 * len(row))
        height = max(l.box[3] - l.box[1] for l in r + s)
        if abs(centre(r) - centre(s)) > 0.5 * height:
            return False
    headings = rows[0][0] + rows[1][0]
    below = [l for side in rows for row in side[1:] for l in row]
    return not any(has_amount(l.text) for l in headings) and any(_MONEY.search(l.text) for l in below)


# A money amount with two decimals (1,200.00 / 1.200,00 / 0.00): a split table has these under its
# headings; an address beside another block has only a street number ("1 King Street").
_MONEY = re.compile(r"(?<![\d/.,])\d{1,3}(?:[,.]\d{3})*[.,]\d{2}(?![\d/.,])")


def looks_tabular(lines: List[Line]) -> bool:
    """A zone laid out as a grid - three or more cells in two or more rows - whatever its label."""
    rows = _rows([lines[i] for i in _xy_cut([l.box for l in lines])])
    return sum(len(r) >= 3 for r in rows) >= 2


def _merge_loose(units: List[Zone]) -> List[Zone]:
    """Lines in no zone that come one after another in reading order make one block."""
    merged: List[Zone] = []
    for u in units:
        if u.label == "other" and merged and merged[-1].label == "other":
            merged[-1].lines += u.lines
        else:
            merged.append(Zone(u.label, u.box, u.score, list(u.lines), u.text, u.note))
    return merged


# ---------------------------------------------------------------------------
# Reading order
# ---------------------------------------------------------------------------

def _xy_cut(boxes: List[Box], indices: Optional[List[int]] = None) -> List[int]:
    """Cuts at the widest gap - horizontal gaps first (bands, top to bottom), then vertical ones
    (columns, left to right) - and orders each side the same way. Cutting at the widest gap first
    keeps a block whole: two blocks side by side are separated before a block's own columns are."""
    indices = list(range(len(boxes))) if indices is None else indices
    if len(indices) <= 1:
        return indices
    for axis in (1, 0):
        halves = _split(boxes, indices, axis)
        if halves:
            return [i for half in halves for i in _xy_cut(boxes, half)]
    return sorted(indices, key=lambda i: (boxes[i][1], boxes[i][0]))


def _split(boxes: List[Box], indices: List[int], axis: int) -> Optional[Tuple[List[int], List[int]]]:
    """The two sides of the widest gap along the axis (0: x, 1: y), or None if there is no gap.
    Touching or overlapping boxes have no gap between them."""
    order = sorted(indices, key=lambda i: boxes[i][axis])
    end, widest, at = boxes[order[0]][axis + 2], 0.0, None
    for k in range(1, len(order)):
        start = boxes[order[k]][axis]
        if start - end > widest:
            widest, at = start - end, k
        end = max(end, boxes[order[k]][axis + 2])
    return (order[:at], order[at:]) if at is not None else None


# ---------------------------------------------------------------------------
# Text of a zone
# ---------------------------------------------------------------------------

def _format(lines: List[Line]) -> List[str]:
    """lines in reading order (each block whole, so columns don't interleave)."""
    out: List[str] = []
    header: Optional[List[Line]] = None
    for row in _rows(lines):
        cells = [c.text.strip() for c in row]
        under_header = header is not None and all(_under(c, header) for c in row)
        if not under_header and len(row) >= 2 and not any(has_amount(c) for c in cells):
            header = row  # headings: the values under them are written with them
            out.append(" | ".join(cells))
            continue
        if header is not None and len(row) >= 2:
            out.append(" | ".join(_with_heading(cell, header) for cell in row))
        else:
            out.append(" | ".join(cells))
    return out


def _pair_amounts(lines: List[Line]) -> List[Line]:
    """On a skewed scan an amount sits a little higher or lower than its label, rows overlap, and the
    zone is read as a column of labels and then a column of amounts. A bare amount left alone in its
    row is put back with the label to its left that it overlaps most (one without amounts, also alone
    in its row): 'Subtotal | 4,687.00'. Rows already read whole are left as they are."""
    alone = [r[0] for r in _rows(lines) if len(r) == 1]
    labels = [l for l in alone if not has_amount(l.text)]
    label_of: Dict[int, Line] = {}
    for amount in (l for l in alone if _bare(l)):
        def shared(l: Line) -> float:
            return min(amount.box[3], l.box[3]) - max(amount.box[1], l.box[1])
        near = [l for l in labels if l.box[2] <= amount.box[0] + 5 and shared(l) > 0
                and not any(l is taken for taken in label_of.values())]
        if near:
            label_of[id(amount)] = max(near, key=shared)
    if len(label_of) < 2:  # one stray amount is no column
        return lines
    amount_of = {id(label): amount for amount_id, label in label_of.items()
                 for amount in lines if id(amount) == amount_id}
    return [Line(f"{l.text.strip()} | {amount_of[id(l)].text.strip()}", _bounds([l.box, amount_of[id(l)].box]),
                 min(l.score, amount_of[id(l)].score)) if id(l) in amount_of else l
            for l in lines if id(l) not in label_of]


def _heading_of(cell: Line, header: List[Line]) -> Optional[Line]:
    """The heading the cell sits under: overlapping it, or aligned with its left or right edge."""
    def overlap(h: Line) -> float:
        return min(cell.box[2], h.box[2]) - max(cell.box[0], h.box[0])
    best = max(header, key=overlap)
    if overlap(best) > 0:
        return best
    nearest = min(header, key=lambda h: abs((h.box[0] + h.box[2]) - (cell.box[0] + cell.box[2])))
    if abs(nearest.box[0] - cell.box[0]) < 40 or abs(nearest.box[2] - cell.box[2]) < 40:  # left- or right-aligned
        return nearest
    return None


def _under(cell: Line, header: List[Line]) -> bool:
    return _heading_of(cell, header) is not None


def _with_heading(cell: Line, header: List[Line]) -> str:
    """'Heading: value' when the cell sits under a heading, else the value."""
    heading = _heading_of(cell, header)
    return f"{heading.text.strip()}: {cell.text.strip()}" if heading else cell.text.strip()


def _rows(lines: List[Line]) -> List[List[Line]]:
    """Consecutive lines (in reading order) at the same height - overlapping by half the smaller height -
    form a row, left to right."""
    rows: List[List[Line]] = []
    for line in lines:
        if rows:
            top, bottom = min(l.box[1] for l in rows[-1]), max(l.box[3] for l in rows[-1])
            shared = min(bottom, line.box[3]) - max(top, line.box[1])
            if shared > 0.5 * min(bottom - top, line.box[3] - line.box[1]):
                rows[-1].append(line)
                continue
        rows.append([line])
    return [sorted(r, key=lambda l: l.box[0]) for r in rows]


# ---------------------------------------------------------------------------
# Boxes
# ---------------------------------------------------------------------------

def _area(b: Box) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _inside(a: Box, b: Box, share: float = 0.8) -> bool:
    """Is most of box a inside box b?"""
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w > 0 and h > 0 and w * h >= share * _area(a) > 0


def _bounds(boxes: List[Box]) -> Box:
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
