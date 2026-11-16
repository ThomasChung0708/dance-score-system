"""Diagnostic Pack evidence. No inferred selection is a score-binding authority."""
from __future__ import annotations

import math
import re
import statistics
from functools import lru_cache

from .ocr_matching import normalize
from .ocr_screens import center, compact


def pack_rows(tokens, region):
    """Join split name boxes on one baseline; counts are not part of names."""
    left, top, right, bottom = region
    lines = []
    for token in sorted(tokens, key=lambda t: (center(t)[1], center(t)[0])):
        x, y = center(token)
        if not (left <= x <= right and top <= y <= bottom):
            continue
        if re.fullmatch(r'\d+', token['text'].strip()):
            continue
        line = next((row for row in lines if abs(center(row[0])[1] - y) <= 5), None)
        if line is None:
            lines.append([token])
        else:
            line.append(token)
    rows = []
    for line in lines:
        line.sort(key=lambda t: center(t)[0])
        text = re.sub(r'\s+\d{1,4}$', '', ' '.join(t['text'].strip() for t in line)).strip()
        points = [p for t in line for p in t['box']]
        evidence = dict(text=text, confidence=min(t['confidence'] for t in line), box=[
            [min(p[0] for p in points), min(p[1] for p in points)],
            [max(p[0] for p in points), min(p[1] for p in points)],
            [max(p[0] for p in points), max(p[1] for p in points)],
            [min(p[0] for p in points), max(p[1] for p in points)],
        ])
        rows.append(dict(text=text, evidence=evidence, tokens=line))
    return rows


def _local_tint_hint(image, rows, region, spacing):
    """Compare chroma prominence only on nearby, demonstrably smooth backgrounds.

    Green subtraction removes common brightness. Centered RGB profiles remove
    row-wide tint before comparing texture; absolute chroma is never sufficient.
    This deliberately covers only matte backgrounds, not arbitrary artwork.
    """
    left, top, right, bottom = region
    inset = max(2, int((right - left) * .05))
    xs = list(range(max(0, int(left) + inset),
                    min(image.width, int(right) - inset),
                    max(1, int((right - left) / 96))))
    band = max(2, int(spacing * .38))
    descriptors = []
    diagnostics = []

    def percentile(values, fraction):
        ordered = sorted(values)
        return ordered[int((len(ordered) - 1) * fraction)]

    for row in rows:
        y = round(center(row['evidence'])[1])
        boxes = [t['box'] for t in row.get('tokens', [row['evidence']])]
        bounds = [(min(p[0] for p in box) - 2, min(p[1] for p in box) - 2,
                   max(p[0] for p in box) + 2, max(p[1] for p in box) + 2)
                  for box in boxes]
        columns = {}
        pixels = []
        for x in xs:
            samples = [image.getpixel((x, sy))
                       for sy in range(y - band, y + band + 1)
                       if max(0, top) <= sy < min(image.height, bottom)
                       and not any(a <= x <= c and b <= sy <= d
                                   for a, b, c, d in bounds)]
            if len(samples) >= 2:
                columns[x] = tuple(statistics.median(p[c] for p in samples)
                                   for c in range(3))
                pixels.extend(samples)
        diagnostic = dict(text=row['text'], tint_comparable=[], tint_margin=None,
                          tint_candidate=False, background_columns=len(columns))
        diagnostics.append(diagnostic)
        # Broad coverage prevents a small clean corner from certifying artwork.
        if len(columns) < max(24, len(xs) * .6):
            descriptors.append(None)
            continue
        rgb = tuple(statistics.median(p[c] for p in pixels) for c in range(3))
        deviations = [max(abs(p[c] - rgb[c]) for c in range(3)) for p in pixels]
        texture = percentile(deviations, .9)
        chromas = [(p[0] - p[1], p[2] - p[1]) for p in pixels]
        chroma = tuple(statistics.median(p[c] for p in chromas) for c in range(2))
        noise = max(1.4826 * statistics.median(abs(p[c] - chroma[c]) for p in chromas)
                    for c in range(2))
        diagnostic.update(background_texture=texture, chroma=list(chroma),
                          chroma_noise=noise)
        # A 90% residual bound rejects textured art even when its median is flat.
        if texture > 12 or noise > 6:
            descriptors.append(None)
            continue
        descriptors.append(dict(y=y, rgb=rgb, chroma=chroma, noise=noise,
                                profile={x: tuple(p[c] - rgb[c] for c in range(3))
                                         for x, p in columns.items()}))

    candidates = []
    for i, descriptor in enumerate(descriptors):
        if descriptor is None:
            continue
        neighbours = []
        for j, other in enumerate(descriptors):
            # Three row gaps plus 0.1 gap for OCR baseline jitter.
            if i == j or other is None or abs(other['y'] - descriptor['y']) > spacing * 3.1:
                continue
            # Similar luminance and tint-invariant spatial profiles, sampled at
            # the same x positions, are required before any colour comparison.
            if abs(statistics.mean(descriptor['rgb']) - statistics.mean(other['rgb'])) > 24:
                continue
            common = descriptor['profile'].keys() & other['profile'].keys()
            if len(common) < max(24, len(xs) * .6):
                continue
            difference = [max(abs(descriptor['profile'][x][c] - other['profile'][x][c])
                              for c in range(3)) for x in common]
            if percentile(difference, .9) <= 12:
                neighbours.append(j)
        diagnostics[i]['tint_comparable'] = [rows[j]['text'] for j in neighbours]
        if len(neighbours) < 2:
            continue
        magnitude = math.hypot(*descriptor['chroma'])
        margins = []
        for j in neighbours:
            other = descriptors[j]
            # Use the full spatial noise, not a standard error that shrinks with
            # pixel count: neighbouring pixels are not independent observations.
            required = max(8, 3 * math.hypot(descriptor['noise'], other['noise']))
            delta = math.dist(descriptor['chroma'], other['chroma'])
            prominence = magnitude - math.hypot(*other['chroma'])
            margins.append(min(prominence / required, delta / max(12, required)))
        diagnostics[i]['tint_margin'] = min(margins)
        if min(margins) > 1 and rows[i]['evidence']['confidence'] >= .9:
            diagnostics[i]['tint_candidate'] = True
            candidates.append(rows[i]['text'])
    return candidates[0] if len(candidates) == 1 else None, diagnostics


def row_hint(image, rows, region):
    """Require unique paired edges or a locally normalized matte-background tint.

    Differences across adjacent scanlines remove slowly changing artwork. Both
    edges must have opposite direction and similar strength, away from text.
    A tint needs a clear margin over ALL comparable local rows (at least two).
    Ordinary shared borders, alike fills, busy artwork and method conflicts abstain.
    This detects a visual candidate, never proves click/hover selection.
    """
    image = image.convert('RGB')
    left, _, right, _ = region
    left, right = max(0, int(left)), min(image.width - 1, int(right))
    ys = sorted(center(r['evidence'])[1] for r in rows)
    spacing = statistics.median([b-a for a, b in zip(ys, ys[1:])]) if len(ys) > 1 else 40
    half = min(24, int(spacing * .49))
    xs = range(left, right, max(1, (right-left)//80))
    diagnostics = []

    @lru_cache(maxsize=None)
    def edge(y):
        differences = [tuple(image.getpixel((x, y+1))[c] - image.getpixel((x, y-1))[c]
                             for c in range(3)) for x in xs]
        if not differences:
            return (0, 0, 0), 0
        vector = tuple(statistics.median(d[c] for d in differences) for c in range(3))
        strength = math.sqrt(sum(v*v for v in vector))
        coherent = sum(sum(d[c]*vector[c] for c in range(3)) >= strength*strength*.65
                       for d in differences) / len(differences)
        return vector, strength if coherent >= .85 else 0

    for row in rows:
        y = round(center(row['evidence'])[1])
        pairs = []
        for offset in range(8, half+1):
            top, bottom = y-offset, y+offset
            if top < 1 or bottom >= image.height-1:
                continue
            a, sa = edge(top)
            for delta in range(-2, 3):
                if not (1 <= bottom+delta < image.height-1):
                    continue
                b, sb = edge(bottom+delta)
                if min(sa, sb) >= 35 and sum(a[c]*b[c] for c in range(3)) < -sa*sb*.8:
                    pairs.append(min(sa, sb))
        diagnostics.append(dict(text=row['text'], edge_score=max(pairs, default=0)))
    ranked = sorted(diagnostics, key=lambda r: r['edge_score'], reverse=True)
    chosen = None
    if ranked and ranked[0]['edge_score'] >= 35 and (
            len(ranked) == 1 or ranked[0]['edge_score'] > ranked[1]['edge_score']*2 + 20):
        row = next(r for r in rows if r['text'] == ranked[0]['text'])
        if row['evidence']['confidence'] >= .9:
            chosen = ranked[0]['text']
    tint, tint_diagnostics = _local_tint_hint(image, rows, region, spacing)
    for diagnostic, colour in zip(diagnostics, tint_diagnostics):
        diagnostic.update({key: value for key, value in colour.items() if key != 'text'})
    if chosen and tint and chosen != tint:
        return None, diagnostics
    return chosen or tint, diagnostics


def infer_selected_pack(selection, catalog):
    """Exact read-only title membership, requiring two distinct songs per Pack."""
    supports = {}
    title_support = {}
    matched = []
    official_titles = set()
    readable_titles = set()
    explicit_custom = (selection.get('category') == 'jinyue'
                       and selection.get('category_confidence', 0) >= .9)
    group_visible = selection.get('pack_list_visible', bool(selection.get('visible_packs')))
    for token in selection.get('song_title_tokens', []):
        if token['confidence'] < .9:
            continue
        ids = catalog.title_ids(token['text'])
        readable_titles.add(normalize(token['text']))
        overlap = any(catalog.songs[sid].get('source_type') == 'official' for sid in ids)
        if overlap:
            official_titles.add(normalize(token['text']))
        if overlap and not (explicit_custom or group_visible):
            continue
        for sid in ids:
            song = catalog.songs[sid]
            pid = song.get('pack_id')
            if pid is not None and song.get('source_type') == 'custom':
                supports.setdefault(pid, set()).add(sid)
                title_support.setdefault(pid, set()).add(normalize(token['text']))
                matched.append(dict(title=token['text'], song_id=sid, pack_id=pid,
                                    confidence=token['confidence']))
    visible = selection.get('visible_packs', [])
    visible_mapping = [dict(text=row['text'], candidates=catalog.visible_pack_candidates(row['text']))
                       for row in visible]
    visible_ids = {p['pack_id'] for row in visible_mapping for p in row['candidates']}
    eligible = {pid for pid, songs in supports.items() if len(songs) >= 2 and len(title_support[pid]) >= 2}
    if (not explicit_custom and not group_visible and readable_titles
            and len(official_titles) > len(readable_titles)/2):
        eligible.clear()
    if selection.get('category') == 'all' and selection.get('category_confidence', 0) >= .9:
        eligible.clear()
    if visible:
        eligible &= visible_ids
    raw_hint = selection.get('pack_row_hint')
    hint_ids = {p['pack_id'] for p in catalog.visible_pack_candidates(raw_hint)} if raw_hint else set()
    hint = raw_hint if len(hint_ids) == 1 else None
    if not hint:
        hint_ids = set()
    reason = 'selected_pack_uncertain'
    chosen = None
    if eligible and hint and not (eligible & hint_ids):
        reason = 'selected_pack_disagreement'
    elif len(eligible) == 1:
        chosen = next(iter(eligible))
    elif len(eligible) > 1 and len(eligible & hint_ids) == 1:
        chosen = next(iter(eligible & hint_ids))
    pack = next((p for p in catalog.packs if p['pack_id'] == chosen), None)
    evidence = dict(matched_titles=matched, candidate_pack_ids=sorted(eligible),
                    official_overlap_titles=sorted(official_titles),
                    visible_pack_ids=sorted(visible_ids), row_hint=hint,
                    visible_pack_mapping=visible_mapping,
                    row_edges=selection.get('pack_row_evidence', []),
                    page=selection.get('song_page'), diagnostic_only=True)
    if pack:
        reason = 'right_song_list_unique_pack' if len(eligible) == 1 else 'right_song_list_and_row_hint'
    else:
        evidence['uncertainty'] = ('visible_packs_missing_from_catalog' if visible and not visible_ids else
                                   'no_pack_with_two_matched_songs' if not eligible else 'ambiguous_or_conflicting_pack')
    selection.update(selected_pack=pack['name'] if pack else None,
                     selected_pack_id=chosen if pack else None,
                     selected_pack_confidence=min((m['confidence'] for m in matched if m['pack_id'] == chosen), default=0),
                     selected_pack_evidence=evidence, pack_reason=reason)
    return selection


def analyze_pack_crop(tokens, image, catalog):
    """Explicit ROI mode, with native pixel coordinates and no screen classifier."""
    headers = [t for t in tokens if compact(t['text']) == 'GROUP']
    top = max((max(p[1] for p in t['box']) for t in headers), default=0) + 6
    region = (0, top, image.width*.89, image.height)
    rows = pack_rows(tokens, region)
    hint, edges = row_hint(image, rows, region)
    selection = dict(visible_packs=rows, pack_list_visible=True, song_title_tokens=[],
                     pack_row_hint=hint, pack_row_evidence=edges)
    return infer_selected_pack(selection, catalog)
