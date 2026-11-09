"""Result-table OCR with explicit local-player evidence and nullable numeric cells.

Coordinates describe the existing skin normalized to 1024x800. Header anchors and
the nickname column gate extraction; names floating in the scene are excluded.
Every game_level is a song difficulty's Chart Level, never an avatar level.
"""
from __future__ import annotations

import re
import copy
from .ocr_screens import center, compact, in_region, value

NUMERIC = ('perfect', 'cool', 'bad', 'miss', 'max_combo', 'game_score')
COLUMNS = {'max_combo': (325, 425), 'perfect': (425, 523), 'cool': (523, 595),
           'bad': (595, 671), 'miss': (671, 746), 'game_score': (837, 940), 'grade': (940, 1024)}


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
            titles.append(value(found[1].strip(), [t]))
        elif compact(t['text']) in ('歌曲名:', '歌曲名'):
            x, y = center(t)
            following = [n for n in footer if x < center(n)[0] < 440 and abs(center(n)[1]-y)<13
                         and not compact(n['text']).startswith('LV') and not re.fullmatch(r'\d+', n['text'])]
            if len(following) == 1:
                titles.append(value(re.sub(r'^[:：]\s*', '', following[0]['text'].strip()), [t, following[0]]))
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
        for key, (left, right) in COLUMNS.items():
            found = in_region(tokens, (left, y-11, right, y+11))
            fields[key] = cell(found, numeric=key != 'grade')
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
        row_fields = {key: cell(in_region(tokens, (left,y-11,right,y+11)), numeric=key!='grade')
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
    final = parse_result(tokens, player_name)
    if grade_reading is not None:
        final['fields']['grade'] = grade_reading
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
                 numeric_roi_evidence=numeric_views,parser_version='phase5f1-v1')
    return final
