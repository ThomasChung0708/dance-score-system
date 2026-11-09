"""Shared live/replay OCR interpretation, with conservative Pack evidence."""
from __future__ import annotations

import copy
import hashlib
import re
import statistics

from .ocr_screens import classify, parse_selection, result_context, room_context, in_region, center, compact, value


def image_hash(image):
    return hashlib.sha256(image.tobytes()+str(image.size).encode()).hexdigest()


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
    rows = []
    for item in selection['visible_packs']:
        _, y = center(item['evidence'])
        # A clear purple fill across most of a row; CD artwork/white text alone must not select a Pack.
        pixels = list(image.crop((52, int(y)-8, 327, int(y)+8)).getdata())
        score = sum(b-r > 16 and b-g > 30 for r, g, b in pixels)/max(1, len(pixels))
        candidates = catalog.visible_pack_candidates(item['text'])
        rows.append(dict(raw_text=item['text'], purple_fraction=score, candidates=candidates,
                         confidence=item['evidence']['confidence']))
    ordered = sorted(rows, key=lambda r: r['purple_fraction'], reverse=True)
    chosen = None
    if ordered and ordered[0]['purple_fraction'] >= .45 and (
            len(ordered) == 1 or ordered[0]['purple_fraction']-ordered[1]['purple_fraction'] >= .25):
        best = ordered[0]
        if len(best['candidates']) == 1 and best['confidence'] >= .9:
            chosen = dict(**best['candidates'][0], status='candidate', confidence_state='CANDIDATE',
                          source='purple_row_unverified_hover_or_selection', raw_text=best['raw_text'],
                          reason='hover_selection_reference_images_missing')
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
            y=self.selected_row(normalized)
            tokens = self.regions(normalized,[(25,60,980,115),(350,130,960,195),
                                              (380,y-14,945,y+15),(40,130,345,512)])
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
    tracker.pack_list_visible = selection['pack_list_visible']
    if selection['pack_list_visible']:
        pack, evidence = active_pack(selection, image.resize((1024, 800)).convert('RGB'), catalog)
        tracker.pack_observation(pack)
        tracker.pack_candidates = evidence
        observation['pack_diagnostics'] = evidence
    source = source_mode if source_mode in ('custom', 'official') else tracker.current_source
    if source == 'official':
        tracker.current_pack = None
    pack_id = (tracker.current_pack or {}).get('pack_id')
    fields = copy.deepcopy(selection['fields'])
    ocr_fields = copy.deepcopy(fields)
    matching = catalog.match(fields, pack_id, source)
    if source == 'official':
        matching = catalog.match_official(fields, source_confirmed=True)
        sid = matching.get('confirmed_song_id')
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
