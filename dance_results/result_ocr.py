"""Result-table OCR with explicit local-player evidence and nullable numeric cells.

Coordinates describe the existing skin normalized to 1024x800. Header anchors and
the nickname column gate extraction; names floating in the scene are excluded.
Every game_level is a song difficulty's Chart Level, never an avatar level.
"""
from __future__ import annotations

import re
import copy
from .ocr_screens import center, compact, in_region, value
from .ocr_policy import AUTO_CONFIDENCE, RESULT_NUMERIC

NUMERIC = RESULT_NUMERIC
COLUMNS = {'max_combo': (325, 425), 'perfect': (425, 523), 'cool': (523, 595),
           'bad': (595, 671), 'miss': (671, 746), 'game_score': (837, 940), 'grade': (940, 1024)}


def row_cell(tokens, left, right, y, *, numeric=True):
    found = in_region(tokens, (left, y-11, right, y+11))
    if not found and numeric:
        # A right-aligned glyph may straddle a column edge after normalization.
        # Accept only one wholly numeric token within the 3px box rounding
        # margin; never widen the ROI into the adjacent accuracy percentage.
        found = [t for t in in_region(tokens, (left-3, y-11, right+3, y+11))
                 if re.fullmatch(r'\d{1,10}', t['text'].strip())
                 and not any(a <= center(t)[0] <= b
                             for a, b in COLUMNS.values() if (a, b) != (left, right))]
    return cell(found, numeric=numeric)


def refine_footer_title(image, engine, original):
    """Separate the cyan label from a merged bilingual footer OCR line.

    The isolated white title is read without a vocabulary or catalog. Conflicting
    page text remains an explicit review reason, even when the line is readable.
    """
    from PIL import Image, ImageOps
    import numpy as np

    # These kana have effectively identical glyphs in this skin. Resolve an
    # isolated script switch inside a hiragana word by its immediate context,
    # without changing voicing, looking up songs or increasing confidence.
    text = original.get('value') or ''
    contextual = re.sub(r'(?<=[ぁ-ゖ])[ヘベペ](?=[ぁ-ゖ])',
                        lambda m: chr(ord(m[0])-0x60), text)
    if contextual != text:
        original = dict(copy.deepcopy(original), value=contextual,
                        method='contextual_homographic_kana', original_reading=copy.deepcopy(original))
    reader = getattr(engine, 'recognize_line', None)
    evidence = original.get('evidence') or []
    if reader is not None and original.get('confidence', 0) < AUTO_CONFIDENCE:
        refined, diagnostics = refine_mixed_title_prefix(image, reader, original)
        if diagnostics:
            return refined, diagnostics
    if reader is None or len(evidence) != 1:
        return original, []
    token = evidence[0]
    if not re.match(r'^歌曲名\s*[:：]\s*\S', token['text']):
        return original, []
    xs, ys = zip(*token['box'])
    box = (int(min(xs)), int(min(ys))-2, int(max(xs))+2, min(800, int(max(ys))+2))
    pixels = np.array(image.crop(box).convert('RGB')).astype('int16')
    r, g, b = [pixels[:, :, i] for i in range(3)]
    cyan = Image.fromarray((((b-r > 60) & (g-r > 30) & (b > 160))*255).astype('uint8'))
    label = cyan.getbbox()
    if not label:
        return original, []
    mask = (pixels.min(axis=2) > 180) & (pixels.max(axis=2)-pixels.min(axis=2) < 65)
    mask[:, :label[2]+2] = False
    ink = Image.fromarray((mask*255).astype('uint8'))
    bounds = ink.getbbox()
    if not bounds:
        return original, []
    view = ImageOps.expand(ImageOps.invert(ink.crop(bounds)), border=3, fill=255).convert('RGB')
    found = reader(view.resize((view.width*3, view.height*3)),
                   origin=(box[0]+bounds[0]-3, box[1]+bounds[1]-3), scale=3)
    diagnostic = [dict(field='title', tokens=found, region=box,
                       preprocessing='isolated_white_footer_title')]
    if len(found) != 1 or found[0]['confidence'] < AUTO_CONFIDENCE or not found[0]['text'].strip():
        return original, diagnostic
    title = found[0]['text'].strip()
    if title == original['value']:
        return original, diagnostic
    field = value(title, found, method='isolated_footer_title',
                  reason='footer_title_page_roi_conflict_requires_review')
    field['original_reading'] = copy.deepcopy(original)
    return field, diagnostic


def refine_mixed_title_prefix(image, reader, original):
    """Read an isolated Latin word before a Japanese footer, without a lexicon.

    A full bilingual line can omit thin Latin strokes. A visible word gap
    supplies the crop boundary; neither catalog matches nor labels set text or
    confidence. Keep the original lower confidence for the untouched suffix.
    """
    import numpy as np

    found = re.fullmatch(r'([A-Za-z1ı|]+)\s*([~〜～][\u3040-\u30ff\u3400-\u9fff]{4,}[~〜～])',
                         original.get('value') or '')
    evidence = original.get('evidence') or []
    if not found or not evidence or original.get('reason'):
        return original, []
    token = evidence[-1]
    xs, ys = zip(*token['box'])
    box = (int(min(xs)), int(min(ys))-2, int(max(xs))+2, min(800, int(max(ys))+2))
    pixels = np.array(image.crop(box).convert('RGB')).astype('int16')
    mask = (pixels.min(axis=2) > 180) & (pixels.max(axis=2)-pixels.min(axis=2) < 65)
    columns = mask.sum(axis=0)
    tall = [i for i, n in enumerate(columns) if n >= 6]
    if not tall:
        return original, []
    start = tall[0]
    end = None
    for x in range(start+2, len(columns)-4):
        if not columns[x:x+4].any():
            end = x
            break
    if end is None or not 10 <= end-start <= 100:
        return original, []
    region = (box[0]+max(0, start-2), box[1], box[0]+end+2, box[3])
    crop = image.crop(region)
    tokens = reader(crop.resize((crop.width*3, crop.height*3)), origin=region[:2], scale=3)
    diagnostic = [dict(field='title', tokens=tokens, region=region,
                       preprocessing='isolated_latin_word_before_japanese')]
    if (len(tokens) != 1 or tokens[0]['confidence'] < .9 or
            not re.fullmatch(r'[A-Za-z]+', tokens[0]['text'].strip())):
        return original, diagnostic
    text = tokens[0]['text'].strip()+' '+found[2]
    field = value(text, [*tokens, *evidence], method='mixed_footer_latin_word_roi')
    field['confidence'] = min(original['confidence'], tokens[0]['confidence'])
    field['original_reading'] = copy.deepcopy(original)
    return field, diagnostic


def incomplete_result(ocr):
    """Missing local row/digits cannot supply complete local score evidence.

    Absence alone does not distinguish animation from a spectator result. Low
    confidence and differing non-null numbers remain consistency evidence.
    """
    return (ocr.get('row') is None or
            any(ocr.get('fields', {}).get(key, {}).get('value') is None for key in NUMERIC))


def complete_reliable_result(ocr):
    """Retain a readable own score independently of song identity confidence.

    This is only a review-evidence gate. Temporal and automatic submission
    continue to require every identity field at AUTO_CONFIDENCE.
    """
    from .ocr_policy import result_required, RESULT_NONFATAL_ERRORS

    fields = ocr.get('fields') or {}
    return (not incomplete_result(ocr) and ocr.get('local_player_status') == 'CONFIRMED'
            and not any(e not in RESULT_NONFATAL_ERRORS for e in ocr.get('errors', []))
            and all(fields.get(k, {}).get('value') is not None
                    and fields[k].get('confidence', 0) >= AUTO_CONFIDENCE
                    and not fields[k].get('reason') for k in result_required(ocr) if k != 'title'))


def superscript_plus_count(mask):
    """Count separate raised crosses to the right of the grade's main letter.

    This only corroborates an OCR suffix; pixels never supply a grade value.
    Requiring every cross prevents treating a clearly drawn A+ as partial A++.
    """
    remaining = {(x, y) for y in range(mask.height) for x in range(mask.width) if mask.getpixel((x, y))}
    components = []
    while remaining:
        start = remaining.pop()
        points = {start}
        pending = [start]
        while pending:
            x, y = pending.pop()
            for neighbor in ((x-1, y), (x+1, y), (x, y-1), (x, y+1)):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    points.add(neighbor)
                    pending.append(neighbor)
        if len(points) >= 5:
            components.append(points)
    if not components:
        return 0

    def bounds(points):
        return (min(x for x, y in points), min(y for x, y in points),
                max(x for x, y in points)+1, max(y for x, y in points)+1)

    boxes = [(points, bounds(points)) for points in components]
    _, (left, top, right, bottom) = max(boxes, key=lambda item: item[1][3]-item[1][1])
    count = 0
    for points, (x0, y0, x1, y1) in boxes:
        width, height = x1-x0, y1-y0
        if not (x0 >= right and .5 <= width/height <= 1.8 and
                height <= (bottom-top)*.75 and y0+y1 < top+bottom):
            continue
        middle_x, middle_y = (x0+x1-1)//2, (y0+y1-1)//2
        horizontal = sum((x, middle_y) in points for x in range(x0, x1))
        vertical = sum((middle_x, y) in points for y in range(y0, y1))
        if horizontal >= width*.8 and vertical >= height*.8:
            count += 1
    return count


def refine_grade(crop, engine, original, page, origin):
    """Bound the skin's orange grade ink, preserving its superscript plus signs.

    Both padded views must agree at the unchanged automatic threshold. Confidence
    is their actual model minimum. Same-letter prefixes are fragments only when
    the pixels corroborate the complete raised plus suffix read by both views.
    """
    from PIL import Image, ImageOps

    reader = getattr(engine, 'recognize_line', None)
    if reader is None or (original['confidence'] >= AUTO_CONFIDENCE and not original.get('reason') and
                          page['value'] in (None, original['value'])):
        return original, []
    mask = Image.new('L', crop.size)
    pixels = crop.convert('RGB').load()
    mask.putdata([
        255 if r > 180 and r > g * 1.3 and g > 65 and b < 100 else 0
        for y in range(crop.height) for x in range(crop.width)
        for r, g, b in (pixels[x, y],)
    ])
    bounds = mask.getbbox()
    if not bounds:
        return original, []
    foreground = ImageOps.invert(mask.crop(bounds)).convert('RGB')
    views = []
    readings = []
    for border in (3, 8):
        view = ImageOps.expand(foreground, border=border, fill='white')
        offset = (origin[0] + bounds[0] - border, origin[1] + bounds[1] - border)
        tokens = reader(view.resize((view.width * 3, view.height * 3)), origin=offset, scale=3)
        views.append(dict(field='grade', tokens=tokens, region=(*origin, origin[0]+crop.width, origin[1]+crop.height),
                          preprocessing='bounded_orange_grade_ink', border=border))
        readings.append(cell(tokens, numeric=False))
    if any(f['value'] is None or f['confidence'] < AUTO_CONFIDENCE or f.get('reason') for f in readings):
        return original, views
    candidates = [f['value'] for f in readings]
    observed = [f['value'] for f in (original, page) if f['value'] is not None]
    candidate = candidates[0]
    plus_count = superscript_plus_count(mask)
    suffix_supported = candidate.endswith('+') and plus_count == candidate.count('+')
    fragments = suffix_supported and all(
        text == candidate or (candidate.startswith(text) and
                              text.rstrip('+') == candidate.rstrip('+'))
        for text in observed)
    if len(set(candidates)) != 1 or (len(set(candidates + observed)) != 1 and not fragments):
        return dict(original, reason='page_and_grade_roi_disagree_requires_review'), views
    return dict(value=readings[0]['value'], confidence=min(f['confidence'] for f in readings),
                evidence=[t for f in readings for t in f['evidence']], reason=None,
                method='two_bounded_orange_grade_views', original_reading=copy.deepcopy(original),
                page_reading=copy.deepcopy(page), superscript_plus_count=plus_count), views


def cell(tokens, numeric=True):
    if not tokens:
        return value(reason='blank_or_unreadable')
    raw = ''.join(t['text'].strip() for t in sorted(tokens, key=lambda t: center(t)[0]))
    if numeric:
        if len(tokens) != 1 or not re.fullmatch(r'\d{1,10}', raw):
            return value(tokens=tokens, reason='ambiguous_or_invalid_integer')
        parsed = int(raw)
    else:
        parsed = compact(raw)
        if not re.fullmatch(r'(?:[A-FS]{1,3}\+{0,3})', parsed):
            return value(tokens=tokens, reason='unrecognized_grade')
    return value(parsed, tokens, reason='low_confidence' if min(t['confidence'] for t in tokens) < .9 else None)


def parse_result(tokens, player_name='TC'):
    fields = {k: value(reason='local_player_not_identified') for k in
              (*NUMERIC, 'grade', 'title', 'game_level', 'completion_status', 'player_name')}
    headers = in_region(tokens, (0, 140, 1024, 230))
    anchors = [t for t in headers if compact(t['text']) in ('PERFECT', 'COOL', 'BAD', 'MISS')]
    supported = len({compact(t['text']) for t in anchors}) == 4
    if not supported:
        return dict(fields=fields, local_player_status='UNKNOWN', multiplayer=None, row=None,
                    player_candidates=[], errors=['result_headers_not_supported'], footer_level_scope='unknown')
    header_y = sum(center(t)[1] for t in anchors)/len(anchors)
    rows = []
    for t in in_region(tokens, (110, header_y+23, 300, 605)):
        _, y = center(t)
        # A real table row has a number at its Combo column at the same baseline.
        neighbors = in_region(tokens, (325, y-10, 425, y+10))
        if any(re.fullmatch(r'\d+', n['text'].strip()) for n in neighbors):
            rows.append(t)
    # Different OCR boxes for the same row are ambiguous, never resolved by ranking.
    local = [t for t in rows if compact(t['text']) == compact(player_name)]
    multi = len(rows) > 1
    footer = in_region(tokens, (0, 740, 1024, 800))
    titles = []
    for t in footer:
        found = re.fullmatch(r'歌曲名\s*[:：]\s*(.+)', t['text'].strip())
        if found:
            title = re.split(r'\s+LV\s*[:：]?\s*\d+\s*$', found[1], flags=re.I)[0].strip()
            if len(title) > 1 or (re.fullmatch(r'[A-Za-z0-9\u3400-\u9fff]', title)
                                  and t['confidence'] >= AUTO_CONFIDENCE):
                titles.append(value(title, [t]))
        elif compact(t['text']) in ('歌曲名:', '歌曲名'):
            x, y = center(t)
            following = [n for n in footer if x < center(n)[0] < 440 and abs(center(n)[1]-y)<13
                         and not compact(n['text']).startswith('LV')
                         and (len(n['text'].strip()) > 1 and not re.fullmatch(r'\d+', n['text'])
                              or re.fullmatch(r'[A-Za-z0-9\u3400-\u9fff]', n['text'].strip())
                              and n['confidence'] >= AUTO_CONFIDENCE)]
            if len(following) == 1 and t['confidence'] >= .9:
                # The label locates the field; only the actual title token
                # measures title confidence. Preserve both measured readings.
                title_field = value(re.sub(r'^[:：]\s*', '', following[0]['text'].strip()),
                                    [following[0]], method='footer_title_token_after_label')
                title_field['anchor_evidence'] = [t]
                titles.append(title_field)
    if len(titles) == 1:
        fields['title'] = titles[0]
    else:
        fields['title'] = value(reason='footer_title_missing_or_ambiguous')
    levels = []
    for t in footer:
        found = re.fullmatch(r'LV[:：]?(\d+)', compact(t['text']))
        if found:
            levels.append(value(int(found[1]), [t]))
        elif compact(t['text']) == 'LV:':
            x, y = center(t)
            following = [n for n in footer if x < center(n)[0] < x+70 and abs(center(n)[1]-y)<12
                         and re.fullmatch(r'\d+', n['text'].strip())]
            if len(following) == 1:
                levels.append(value(int(following[0]['text']), [t, following[0]]))
    fields['game_level'] = levels[0] if len(levels) == 1 else value(reason='footer_level_missing_or_ambiguous')
    scope = 'host_only_not_local_chart' if multi else 'local_result_display'
    errors = []
    row = None
    status = 'UNKNOWN'
    if len(local) == 1:
        name = local[0]; _, y = center(name)
        row = dict(y=y, name_box=name['box'])
        fields['player_name'] = value(name['text'].strip(), [name],
                                      reason='low_confidence' if name['confidence'] < .9 else None)
        badges = [t for t in in_region(tokens, (0, y-32, 1024, y+32))
                  if compact(t['text']) in ('FULLCOMBO', 'ALLPERFECT')]
        if len(badges) == 1:
            fields['result_status'] = value(
                'ALL PERFECT' if compact(badges[0]['text']) == 'ALLPERFECT' else 'FULL COMBO', badges)
        elif badges:
            errors.append('ambiguous_local_result_status')
        for key, (left, right) in COLUMNS.items():
            fields[key] = row_cell(tokens, left, right, y, numeric=key != 'grade')
        markers = [t for t in in_region(tokens, (0, y-32, 57, y+32))
                   if compact(t['text']) in ('GAME', 'OVER', 'GAMEOVER')]
        failed = any('OVER' in compact(t['text']) and t['confidence'] >= .9 for t in markers)
        if name['confidence'] >= .9:
            status = 'CONFIRMED'
            fields['completion_status'] = value('FAILED' if failed else 'COMPLETED',
                [name, *markers], method='local_row_gameover_badge' if failed else 'local_result_row_without_gameover')
        else:
            errors.append('local_player_low_confidence')
    else:
        errors.append('local_player_missing' if not local else 'multiple_local_player_rows')
    if multi:
        errors.append('footer_level_is_host_not_local')
    player_rows = []
    for index, name in enumerate(rows):
        _, y = center(name)
        row_fields = {key: row_cell(tokens, left, right, y, numeric=key!='grade')
                      for key,(left,right) in COLUMNS.items()}
        markers = [t for t in in_region(tokens,(0,y-32,57,y+32))
                   if compact(t['text']) in ('GAME','OVER','GAMEOVER')]
        failed = any('OVER' in compact(t['text']) and t['confidence']>=.9 for t in markers)
        row_fields['player_name'] = value(name['text'].strip(),[name])
        row_fields['completion_status'] = value('FAILED' if failed else 'COMPLETED',[name,*markers],
                                               method='result_table_row_badge')
        player_rows.append(dict(row_id=f'row-{index}',name=name['text'].strip(),confidence=name['confidence'],
                                fields=row_fields,y=y))
    return dict(fields=fields, local_player_status=status, multiplayer=multi, row=row, player_rows=player_rows,
                requested_player_name=player_name,
                player_candidates=[dict(text=t['text'], confidence=t['confidence'], box=t['box']) for t in rows],
                errors=errors, footer_level_scope=scope)


def recognize_result(image, engine, player_name='TC', tokens=None):
    """Reuse the monitor's full OCR; re-read only missing/unreliable own-row cells."""
    normalized = image.convert('RGB').resize((1024, 800))
    tokens = list(engine.recognize(normalized) if tokens is None else tokens)
    result = parse_result(tokens, player_name)
    raw_tokens=copy.deepcopy(tokens);raw_fields=copy.deepcopy(result['fields'])
    rereads = []
    grade_reading = None
    if result['row']:
        y = result['row']['y']
        boxes = dict(COLUMNS)
        boxes['player_name'] = (112, 300)
        for key, (left, right) in boxes.items():
            field = result['fields'][key]
            if key != 'grade' and field['value'] is not None and field['confidence'] >= .9 and not field.get('reason'):
                continue
            box = (left, int(y)-(35 if key == 'grade' else 15), right, int(y)+(29 if key == 'grade' else 16))
            crop = normalized.crop(box)
            recognize = engine.recognize if key == 'player_name' else getattr(engine, 'recognize_line', engine.recognize)
            found = recognize(crop.resize((crop.width*3, crop.height*3)), origin=box[:2], scale=3)
            rereads.append(dict(field=key, tokens=found, region=box))
            if key in NUMERIC and (cell(found)['value'] is None or cell(found)['confidence'] < .9):
                # Dim names behind the translucent table can occlude small white digits.
                # Threshold the evidence pixels, never substitute another player's value.
                filtered = crop.convert('L').point(lambda pixel: 0 if pixel >= 180 else 255).convert('RGB')
                retry = recognize(filtered.resize((crop.width*3, crop.height*3)), origin=box[:2], scale=3)
                rereads.append(dict(field=key, tokens=retry, region=box, preprocessing='luminance_threshold_180'))
                reading = cell(retry)
                if reading['value'] is not None and reading['confidence'] >= .9:
                    found = retry
            if key == 'grade':
                grade_reading = cell(found, numeric=False)
                grade_reading['method'] = 'full_height_grade_cell_recognition'
                if field['value'] is not None and grade_reading['value'] != field['value']:
                    grade_reading['reason'] = 'page_and_grade_roi_disagree_requires_review'
                grade_reading, grade_views = refine_grade(crop, engine, grade_reading, field, box[:2])
                rereads.extend(grade_views)
            # Preserve contradictions, rather than selecting the most convenient reading.
            if key == 'player_name':
                valid = len(found) == 1 and compact(found[0]['text']) == compact(player_name) and found[0]['confidence'] >= .9
            else:
                reading = cell(found, numeric=key != 'grade')
                valid = reading['value'] is not None and reading['confidence'] >= .9 and not reading.get('reason')
                valid = valid and (field['value'] is None or reading['value'] == field['value'])
            if valid:
                tokens = [t for t in tokens if not (left <= center(t)[0] <= right and abs(center(t)[1]-y) <= 16)]
                tokens.extend(found)
            elif key == 'player_name' and field.get('evidence'):
                # The nickname is a known single line, often too small for the
                # detector in a wide, mostly blank cell. Bound the foreground
                # inside its original text box before single-line recognition.
                from PIL import ImageOps
                points = [p for t in field['evidence'] for p in t['box']]
                name_box = (int(min(p[0] for p in points))-3, int(min(p[1] for p in points))-3,
                            int(max(p[0] for p in points))+4, int(max(p[1] for p in points))+4)
                mask = normalized.crop(name_box).convert('L').point(lambda pixel: 255 if pixel >= 180 else 0)
                bounds = mask.getbbox()
                line_reader = getattr(engine, 'recognize_line', None)
                if bounds and line_reader:
                    foreground = ImageOps.expand(ImageOps.invert(mask.crop(bounds)), border=3, fill=255).convert('RGB')
                    origin = (name_box[0]+bounds[0]-3, name_box[1]+bounds[1]-3)
                    retry = line_reader(foreground.resize((foreground.width*3, foreground.height*3)),
                                        origin=origin, scale=3)
                    rereads.append(dict(field=key, tokens=retry, region=name_box,
                                        preprocessing='bounded_bright_foreground_line'))
                    if len(retry) == 1 and compact(retry[0]['text']) == compact(player_name) and retry[0]['confidence'] >= .9:
                        tokens = [t for t in tokens if not (left <= center(t)[0] <= right and abs(center(t)[1]-y) <= 16)]
                        tokens.extend(retry)
    # The page detector can omit a one-character title. Re-read only the space
    # immediately following an observed footer label, bounded by the LV anchor.
    if result['fields']['title']['value'] is None:
        labels = [t for t in in_region(tokens, (0, 740, 440, 800))
                  if compact(t['text']) in ('歌曲名:', '歌曲名') and t['confidence'] >= AUTO_CONFIDENCE]
        reader = getattr(engine, 'recognize_line', None)
        if len(labels) == 1 and reader:
            label = labels[0]
            left = int(max(p[0] for p in label['box'])) + 2
            y = center(label)[1]
            ends = [min(p[0] for p in t['box']) for t in in_region(tokens, (left, 740, 440, 800))
                    if compact(t['text']).startswith('LV')]
            right = int(min(ends, default=min(left+140, 440))) - 4
            box = (left, max(740, int(y)-16), right, min(800, int(y)+16))
            if right > left:
                crop = normalized.crop(box)
                found = reader(crop.resize((crop.width*3, crop.height*3)), origin=box[:2], scale=3)
                rereads.append(dict(field='title', tokens=found, region=box,
                                    preprocessing='footer_title_after_label'))
                if (len(found) == 1 and found[0]['confidence'] >= AUTO_CONFIDENCE
                        and re.fullmatch(r'[A-Za-z0-9\u3400-\u9fff]', found[0]['text'].strip())):
                    tokens.extend(found)
    final = parse_result(tokens, player_name)
    final['fields']['title'], title_views = refine_footer_title(normalized, engine, final['fields']['title'])
    rereads.extend(title_views)
    if grade_reading is not None:
        final['fields']['grade'] = grade_reading
        for own in final.get('player_rows', []):
            if final['row'] and abs(own['y'] - final['row']['y']) < 1 and compact(own['name']) == compact(player_name):
                own['fields']['grade'] = copy.deepcopy(grade_reading)
    from .ocr_numeric import refine_number,footer_level_box
    numeric_views={}
    if final['row'] and final['local_player_status']=='CONFIRMED':
        y=final['row']['y']
        own=[r for r in final['player_rows'] if compact(r['name'])==compact(player_name) and abs(r['y']-y)<1]
        if len(own)==1:
            for key in NUMERIC:
                left,right=COLUMNS[key]
                field,views=refine_number(normalized,engine,(left,int(y)-15,right,int(y)+16),final['fields'][key])
                if views:numeric_views[key]=views
                final['fields'][key]=field;own[0]['fields'][key]=copy.deepcopy(field)
    box=footer_level_box(final['fields']['game_level'])
    if box:
        final['fields']['game_level'],views=refine_number(normalized,engine,box,final['fields']['game_level'])
        if views:numeric_views['game_level']=views
    final.update(tokens=tokens,roi_rereads=rereads,raw_tokens=raw_tokens,raw_fields=raw_fields,
                 numeric_roi_evidence=numeric_views,parser_version='phase5f1-v1',
                 incomplete=incomplete_result(final))
    return final
