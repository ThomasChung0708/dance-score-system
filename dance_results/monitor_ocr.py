"""Shared live/replay OCR interpretation, with conservative Pack evidence."""
from __future__ import annotations

import copy
from collections import deque
import hashlib
import re
import statistics

from .ocr_screens import classify, parse_selection, result_context, room_context, in_region, center, compact, value


def image_hash(image):
    return hashlib.sha256(image.tobytes()+str(image.size).encode()).hexdigest()


class SelectionFrameBuffer:
    """Short, bounded pixel lookback for selection frames skipped by OCR cadence."""
    def __init__(self):
        self.frames = deque(maxlen=6)
        self.last_sample = float('-inf')

    def clear(self):
        self.frames.clear()
        self.last_sample = float('-inf')

    def observe(self, frames, now):
        for sampled, image in sorted(frames, key=lambda frame: frame[0]):
            if sampled <= self.last_sample:
                continue
            self.last_sample = sampled
            if not 0 <= now-sampled <= 10:
                continue
            normalized = image.convert('RGB').resize((1024, 800))
            center_y = Analyzer.selected_row(normalized)
            if center_y is None:
                continue
            # Reject broad uniform/background bands: a highlighted song row
            # has an edge within 30px, unlike ordinary gameplay backgrounds.
            def active(y):
                return statistics.mean(b-r for r, g, b in
                    (normalized.getpixel((x, y)) for x in (776, 781, 786))) > -30
            if active(max(235, center_y-30)) and active(min(569, center_y+30)):
                continue
            self.frames.append((sampled, normalized))
        while self.frames and now-self.frames[0][0] > 10:
            self.frames.popleft()

    def before(self, sampled):
        return [(at, image) for at, image in reversed(self.frames) if 0 < sampled-at <= 10]


def black_or_loading_frame(image):
    """Ignore uniformly near-black game content, excluding window chrome.

    The brightest downsampled content pixel must stay below 6% luminance; a
    normal dark scene with visible HUD/text remains eligible for classification.
    This is an abstention, never evidence of song identity or a result.
    """
    body = image.crop((0, int(image.height*.06), image.width, int(image.height*.94)))
    return body.convert('L').resize((32, 24)).getextrema()[1] < 16


def change_signature(image):
    # Limit expensive OCR trigger comparison to meaningful UI regions, not the animated centre background.
    normalized = image.resize((1024, 800)).convert('L')
    return [normalized.crop(region).resize((64, 20)) for region in
            [(25, 60, 980, 220), (30, 230, 980, 560), (0, 650, 1024, 800)]]


def change_amount(before, after):
    from PIL import ImageChops, ImageStat
    if before is None:
        return 255.0
    return max(ImageStat.Stat(ImageChops.difference(a, b)).mean[0] for a, b in zip(before, after))


def active_pack(selection, image, catalog):
    from .pack_selection import row_hint
    if 'pack_row_evidence' in selection:
        hint, edges = selection.get('pack_row_hint'), selection['pack_row_evidence']
    else:
        hint, edges = row_hint(image, selection['visible_packs'], (52, 208, 327, 512))
    rows = [dict(raw_text=row['text'], candidates=catalog.visible_pack_candidates(row['text']),
                 confidence=row['evidence']['confidence'], **edge)
            for row, edge in zip(selection['visible_packs'], edges)]
    chosen = None
    for row in rows:
        if row['raw_text'] == hint and len(row['candidates']) == 1 and row['confidence'] >= .9:
            chosen = dict(**row['candidates'][0], status='candidate', confidence_state='CANDIDATE',
                          source='local_paired_row_edges', raw_text=row['raw_text'],
                          reason='visual_hint_not_selection_authority')
    return chosen, rows


def playing_preview(tokens):
    bottom = in_region(tokens, (0, 740, 1024, 800))
    title = None; level = None
    for t in bottom:
        raw = t['text']
        found = re.search(r'歌曲名\s*[:：]\s*(.+)', raw)
        if found and t['confidence'] >= .9:
            title = found[1].strip()
        if compact(raw) in ('歌曲名:', '歌曲名'):
            x, y = center(t)
            following = [n for n in bottom if center(n)[0] > x and center(n)[0] < 280 and
                         abs(center(n)[1]-y) < 15 and n['confidence'] >= .9 and 'LV' not in compact(n['text'])]
            if len(following) == 1:
                title = following[0]['text'].strip()
        found = re.fullmatch(r'LV[:：]?(\d+)', compact(raw))
        if found and t['confidence'] >= .9:
            level = int(found[1])
        elif compact(raw) == 'LV:':
            x, y = center(t)
            numbers = [n for n in bottom if x < center(n)[0] < x+70 and abs(center(n)[1]-y) < 12 and
                       re.fullmatch(r'\d+', n['text']) and n['confidence'] >= .9]
            if len(numbers) == 1:
                level = int(numbers[0]['text'])
    return dict(title=title, game_level=level)


def room_preview(tokens):
    titles=[t for t in in_region(tokens,(830,420,1024,455)) if t['confidence']>=.9]
    title=' '.join(t['text'].strip() for t in sorted(titles,key=lambda t:center(t)[0])) or None
    level=None
    for t in in_region(tokens,(830,390,1024,420)):
        found=re.search(r'難度(\d+)',compact(t['text']))
        if found and t['confidence']>=.9:level=int(found[1])
    difficulties=[compact(t['text']) for t in in_region(tokens,(850,645,1024,690))
                  if compact(t['text']) in ('EASY','NORMAL','HARD') and t['confidence']>=.9]
    return dict(title=title,game_level=level,difficulty=difficulties[0] if len(difficulties)==1 else None)


class Analyzer:
    def __init__(self, engine, player_name='TC'):
        self.engine = engine
        self.player_name = player_name
        self.calls = 0

    def regions(self, image, boxes):
        """One compact model call, then restore original normalized coordinates."""
        from PIL import Image
        height = sum(b[3]-b[1]+16 for b in boxes)
        mosaic = Image.new('RGB', (1024, height), 'white')
        layout = [];cursor = 8
        for box in boxes:
            mosaic.paste(image.crop(box), (box[0], cursor))
            layout.append((cursor, cursor+box[3]-box[1], box))
            cursor += box[3]-box[1]+16
        found = self.engine.recognize(mosaic);self.calls += 1
        result = []
        for token in found:
            x, y = center(token)
            for top, bottom, box in layout:
                if box[0] <= x <= box[2] and top <= y <= bottom:
                    result.append(dict(token,box=[[px,py-top+box[1]] for px,py in token['box']]))
                    break
        return result

    @staticmethod
    def selected_row(image):
        bands=[];start=None
        for y in range(235,570):
            pixels=[image.getpixel((x,y)) for x in (776,781,786)]
            active=statistics.mean(b-r for r,g,b in pixels)>-30
            if active and start is None:start=y
            if not active and start is not None:
                if y-start>=10:bands.append((start+y)//2)
                start=None
        if start is not None and 570-start>=10:bands.append((start+570)//2)
        return bands[0] if len(bands)==1 else None

    def analyze(self, image, state='UNKNOWN', full=True):
        normalized = image.convert('RGB').resize((1024, 800))
        roi_mode = not full and state in ('PLAYING','SONG_SELECT')
        if roi_mode and state == 'PLAYING':
            tokens = self.regions(normalized,[(0,150,1024,225),(0,640,1024,800)])
        elif roi_mode and self.selected_row(normalized) is not None:
            tokens = self.regions(normalized,[(25,60,980,115),(350,130,960,195),
                                              (380,220,945,650),(40,130,345,512)])
        else:
            tokens = self.engine.recognize(normalized)
            self.calls += 1
            roi_mode=False
        screen, anchors = classify(tokens)
        # Results need the complete player's row; unknown ROI needs a full classification pass.
        if roi_mode and screen in ('results', 'unknown'):
            tokens = self.engine.recognize(normalized)
            self.calls += 1
            screen, anchors = classify(tokens)
        observation = self.from_tokens(tokens, normalized, screen, anchors, roi_mode)
        if roi_mode and screen == 'song_select' and any(observation['selection']['fields'][k]['value'] is None
                for k in ['title','difficulty','game_level','duration_seconds']):
            fallback=self.analyze(image,state,full=True)
            fallback['roi_fallback_reason']='selected_row_fields_incomplete'
            return fallback
        if screen == 'song_select':
            observation['selection']['raw_fields']=copy.deepcopy(observation['selection']['fields'])
            title = observation['selection']['fields']['title']
            # NEW! badges sit to the left of the fixed song-text column and may merge
            # into its OCR box. Re-read pixels from the text column, never strip a prefix.
            if title['value'] and title['evidence'] and min(p[0] for p in title['evidence'][0]['box']) < 440:
                _, y = center(title['evidence'][0])
                box = (453, int(y)-13, 790, int(y)+14)
                crop = normalized.crop(box)
                reread = self.engine.recognize(crop.resize((crop.width*2, crop.height*2)), origin=box[:2], scale=2)
                self.calls += 1
                if len(reread) == 1 and reread[0]['confidence'] >= .9:
                    observation['selection']['fields']['title'] = value(reread[0]['text'], reread,
                        method='selected_row_text_roi_excluding_badge')
                else:
                    observation['selection']['fields']['title'] = value(reason='selected_title_badge_or_clipping_uncertain')
            from .ocr_numeric import refine_number
            bpm=observation['selection']['fields']['bpm']
            evidence=bpm.get('evidence') or []
            if evidence and bpm.get('value') is not None and not bpm.get('reason'):
                points=[p for t in evidence for p in t['box']]
                box=(int(min(p[0]for p in points))-2,int(min(p[1]for p in points))-2,
                     int(max(p[0]for p in points))+2,int(max(p[1]for p in points))+2)
                field,views=refine_number(normalized,self.engine,box,bpm,style='cyan',decimal=True)
                observation['selection']['fields']['bpm']=field
                if views:
                    observation['selection']['numeric_roi_evidence']={'bpm':views}
                    self.calls+=sum('view_sha256' in view for view in views)
        if screen == 'waiting_room':
            from .lobby_download_ocr import lobby_download,reread_own_badge
            badges=reread_own_badge(self.engine,normalized,tokens,self.player_name)
            if badges:self.calls+=1
            observation['download_lobby']=lobby_download(tokens,normalized,self.player_name,badges)
        if screen == 'results':
            from .result_ocr import recognize_result
            observation['result_ocr'] = recognize_result(normalized, self.engine, self.player_name, tokens=tokens)
            self.calls += len(observation['result_ocr']['roi_rereads'])
            self.calls += sum('view_sha256' in v for views in observation['result_ocr']['numeric_roi_evidence'].values() for v in views)
        return observation

    def from_tokens(self, tokens, normalized, screen=None, anchors=None, roi_mode=False):
        if screen is None:
            screen, anchors = classify(tokens)
        state = {'song_select': 'SONG_SELECT', 'results': 'RESULT', 'gameplay': 'PLAYING',
                 'waiting_room': 'WAITING_ROOM', 'lobby': 'LOBBY', 'profile': 'PROFILE', 'login': 'LOGIN',
                 'spectating':'SPECTATING'}.get(screen, 'UNKNOWN')
        observation = dict(state=state, screen_type=screen, frame_hash=image_hash(normalized), anchors=anchors,
                           tokens=tokens, roi_mode=roi_mode)
        if screen == 'song_select':
            observation['selection'] = parse_selection(tokens, normalized)
        elif screen == 'results':
            context = result_context(tokens, self.player_name)
            observation['context'] = context
            from .result_ocr import parse_result
            observation['result_ocr'] = parse_result(tokens, self.player_name)
            if context['local_player_status'] == 'failed':
                observation['state'] = 'GAME_OVER'
        elif screen == 'gameplay':
            observation['playing_preview'] = playing_preview(tokens)
        elif screen == 'waiting_room':
            observation['context'] = room_context(tokens, self.player_name)
            observation['room_preview'] = room_preview(tokens)
            from .lobby_download_ocr import lobby_download
            observation['download_lobby']=lobby_download(tokens,normalized,self.player_name)
        return observation


def apply_selection(observation, image, tracker, catalog, references=None, source_mode='auto'):
    if observation['state'] != 'SONG_SELECT':
        return observation
    selection = observation['selection']
    from .pack_selection import infer_selected_pack
    infer_selected_pack(selection, catalog)
    tracker.pack_list_visible = selection['pack_list_visible']
    inferred = None
    if selection.get('pack_reason') in ('right_song_list_unique_pack', 'right_song_list_and_row_hint'):
        pack = next((p for p in catalog.packs if p['pack_id'] == selection['selected_pack_id']), None)
        if pack:
            inferred = dict(pack, status='confirmed', source=selection['pack_reason'],
                            evidence=copy.deepcopy(selection['selected_pack_evidence']))
    if inferred or selection['pack_list_visible']:
        tracker.pack_observation(inferred, inferred=bool(inferred))
    if selection['pack_list_visible']:
        _, evidence = active_pack(selection, image.resize((1024, 800)).convert('RGB'), catalog)
        tracker.pack_candidates = evidence
        observation['pack_diagnostics'] = evidence
    source = source_mode if source_mode in ('custom', 'official') else tracker.current_source
    pack_id = (tracker.current_pack or {}).get('pack_id')
    if source == 'official':
        tracker.current_pack = None
        pack_id = inferred['pack_id'] if inferred else None
    fields = copy.deepcopy(selection['fields'])
    ocr_fields = copy.deepcopy(fields)
    matching = catalog.match_identity(fields, pack_id, source)
    sid = matching.get('confirmed_song_id')
    if source not in ('custom', 'official') and sid is not None:
        source = catalog.songs[sid]['source_type']
    if source == 'official':
        official = catalog.match_official(fields, source_confirmed=True)
        if official.get('confirmed_song_id') is not None and (pack_id is None or sid is not None):
            matching = official
        sid = matching.get('confirmed_song_id') if official.get('confirmed_song_id') is not None else None
        if sid is not None:
            gd = catalog.difficulties[(sid, fields['difficulty']['value'])]
            for key in ('game_level', 'total_notes'):
                if fields.get(key, {}).get('value') is None:
                    fields[key] = dict(value=gd[key], confidence=1.0, evidence=[],
                                       method='official_catalog_fill', reason=None)
    if references and source == 'custom':
        row = references.safe_lookup(fields, pack_id, source, catalog, matching)
        if row:
            matching.update(status='confirmed_manual_reference', confirmed_song_id=row['song_id'], reference=row,
                            prior_automatic_reasons=matching.get('reasons',[]),reasons=[])
    observation.update(fields=copy.deepcopy(fields), ocr_fields=ocr_fields, source_type=source, matching=matching)
    for key,source in [('raw_ocr_fields','raw_fields'),('numeric_roi_evidence','numeric_roi_evidence')]:
        if source in selection:observation[key]=copy.deepcopy(selection[source])
    return observation
