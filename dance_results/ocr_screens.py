"""Static screenshot interpretation for the supplied 1024x800 Windows game skin.

OCR text + relative screen regions + visible highlight colour; no filenames, labels,
expected values, database LV or source-chart difficulty are used for extraction.
"""
from __future__ import annotations

import re
import statistics
import unicodedata

DIFFICULTIES = ('EASY', 'NORMAL', 'HARD')


def center(token):
    return tuple(sum(p[i] for p in token['box'])/4 for i in (0, 1))


def in_region(tokens, region):
    x0, y0, x1, y1 = region
    return sorted([t for t in tokens if x0 <= center(t)[0] <= x1 and y0 <= center(t)[1] <= y1],
                  key=lambda t: (center(t)[1], center(t)[0]))


def compact(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text)).upper()


def duration_seconds(text):
    match = re.fullmatch(r'(\d{1,3}):([0-5]\d)', unicodedata.normalize('NFKC', text).strip())
    if not match:
        return None
    value = int(match[1])*60+int(match[2])
    return value if value > 0 else None


def value(number=None, tokens=(), reason=None, method='ocr'):
    return {'value': number, 'confidence': min((t['confidence'] for t in tokens), default=0.0),
            'evidence': list(tokens), 'method': method, 'reason': reason}


def classify(tokens):
    text = '|'.join(compact(t['text']) for t in tokens)
    if 'INVENTORY' in text or '更衣間' in text:
        return 'inventory', ['inventory overlay; underlying room metadata is occluded']
    if 'PERSONALINFORMATION' in text or '個人資料' in text:
        return 'profile', ['PERSONAL INFORMATION / 個人資料']
    if all(w in text for w in DIFFICULTIES) and 'SETTING' in text:
        return 'song_select', ['SETTING', 'EASY / NORMAL / HARD']
    if all(w in text for w in ['PERFECT', 'COOL', 'BAD', 'MISS']) and '保存' in text:
        return 'results', ['judgment table', '保存錄像']
    if 'KEYBOARD' in text and ('LOGIN' in text or 'DANCINGPAD' in text):
        return 'login', ['KEYBOARD', 'LOGIN / DANCING PAD']
    if 'WAITING' in text and ('舞台' in text or '快速進入' in text):
        return 'lobby', ['WAITING', 'stage list']
    if 'SPEED' in text and ('難度' in text or '準備' in text or '開始' in text):
        return 'waiting_room', ['SPEED', 'room controls']
    spectator_banner=''.join(compact(t['text']) for t in sorted(in_region(tokens,(0,24,700,100)),key=lambda t:center(t)[0]) if t['confidence']>=.9)
    if 'QUITLOOKONMODE' in text or 'QUITLOOKONMODE' in spectator_banner:
        return 'spectating', ['quit look on mode spectator banner']
    if '歌曲名' in text and '時間' in text:
        return 'gameplay', ['歌曲名', '時間']
    return 'unknown', ['insufficient supported screen anchors']


def bpm_field(info):
    """Join a BPM label to one adjacent number, retaining both OCR anchors."""
    readings=[];missing=[]
    def bounds(t):
        return (min(p[0]for p in t['box']),min(p[1]for p in t['box']),
                max(p[0]for p in t['box']),max(p[1]for p in t['box']))
    for label in info:
        text=compact(label['text'])
        # Preserve the established merged-token parser (including merged metadata).
        joined=re.search(r'BPM[:：]?(\d+(?:\.\d+)?)',text)
        if joined and float(joined[1])>0:
            readings.append(value(float(joined[1]),[label]));continue
        if text.rstrip(':：')!='BPM':continue
        left,top,right,bottom=bounds(label);height=max(1,bottom-top)
        candidates=[]
        for token in info:
            number=re.fullmatch(r'[:：]?(\d+(?:\.\d+)?)',compact(token['text']))
            if not number or float(number[1])<=0:continue
            x,y,x1,y1=bounds(token);h=max(1,y1-y)
            overlap=min(bottom,y1)-max(top,y)
            if right-3<=x<=right+height*2.5 and overlap>=min(height,h)*.5 and abs(center(token)[1]-center(label)[1])<=max(height,h)*.5:
                candidates.append((token,float(number[1])))
        if len(candidates)!=1:
            missing.extend([label,*[t for t,n in candidates]])
            if len(candidates)>1:return value(tokens=missing,reason='bpm_pair_ambiguous')
            continue
        token,number=candidates[0];x=bounds(token)[0]
        # Another field label between BPM and the number must not be skipped.
        blockers=[t for t in info if t is not label and t is not token and
                  right-3<center(t)[0]<x and abs(center(t)[1]-center(label)[1])<=height*.5 and
                  compact(t['text']) not in (':','：')]
        if blockers:
            missing.extend([label,token,*blockers]);continue
        reason='bpm_label_low_confidence' if label['confidence']<.9 else None
        readings.append(value(number,[label,token],reason=reason,method='ocr_bpm_label_value_pair'))
    if len(readings)==1:return readings[0]
    if len(readings)>1:return value(tokens=[t for f in readings for t in f['evidence']],reason='bpm_observations_ambiguous')
    return value(tokens=missing,reason='bpm_value_missing' if missing else 'not_readable')


def parse_selection(tokens, image):
    fields = {k: value(reason='not_readable') for k in
              ['title', 'difficulty', 'game_level', 'total_notes', 'bpm', 'duration_seconds', 'artist']}
    # Coordinates refer to a normalized full screenshot, including its narrow window title bar.
    tabs = [t for t in in_region(tokens, (350, 120, 950, 195)) if compact(t['text']) in DIFFICULTIES]
    tab_scores = []
    for t in tabs:
        x, y = center(t); x = int(min(p[0] for p in t['box'])-22)
        patch = image.crop((x-3, int(y)-3, x+4, int(y)+4))
        tab_scores.append((statistics.mean(p[1] for p in patch.getdata()), t))
    tab_scores.sort(key=lambda s: s[0], reverse=True)
    if len(tab_scores) == 3 and tab_scores[0][0]-tab_scores[1][0] > 35:
        fields['difficulty'] = value(compact(tab_scores[0][1]['text']), [tab_scores[0][1]],
                                     method='ocr_tab_label_and_active_background')
    titles = [t for t in in_region(tokens, (410, 230, 790, 570))
              if not re.fullmatch(r'[\d :/]+', t['text'])]
    rows = []
    for title in titles:
        _, y = center(title)
        row = in_region(tokens, (405, y-11, 945, y+11))
        times = [t for t in row if 795 < center(t)[0] < 885 and duration_seconds(t['text']) is not None]
        levels = [t for t in row if center(t)[0] > 890 and re.fullmatch(r'\d{1,3}', t['text'].strip())]
        if len(times) != 1 or len(levels) != 1:
            continue
        patch = image.crop((774, int(y)-3, 789, int(y)+4))
        purple = statistics.mean(p[2]-p[0] for p in patch.getdata())
        rows.append({'title': title, 'time': times[0], 'level': levels[0], 'highlight_score': purple})
    ranked = sorted(rows, key=lambda r: r['highlight_score'], reverse=True)
    if ranked and ranked[0]['highlight_score'] > -30 and (len(ranked) == 1 or
            ranked[0]['highlight_score']-ranked[1]['highlight_score'] > 30):
        row = ranked[0]
        fields['title'] = value(row['title']['text'], [row['title']], method='ocr_highlighted_row')
        fields['game_level'] = value(int(row['level']['text']), [row['level']])
        fields['duration_seconds'] = value(duration_seconds(row['time']['text']), [row['time']])
    info = in_region(tokens, (45, 410, 340, 510))
    fields['bpm']=bpm_field(info)
    for t in info:
        text = compact(t['text'])
        notes = re.search(r'音符[數数][:：]?(\d+)', text)
        if notes and int(notes[1]) > 0:
            fields['total_notes'] = value(int(notes[1]), [t])
        artist = re.search(r'演唱者\s*[:：]\s*(.+)', t['text'])
        if artist:
            fields['artist'] = value(artist[1].strip(), [t])
    # The backend sometimes splits label and value into separate boxes.
    for label in info:
        if compact(label['text']).rstrip(':：') == '演唱者':
            matches = [t for t in info if center(t)[0] > center(label)[0] and
                       abs(center(t)[1]-center(label)[1]) < 12]
            if len(matches) == 1:
                fields['artist'] = value(matches[0]['text'].strip(), [label, matches[0]])
    overlay = any(compact(t['text']) == 'GROUP' for t in in_region(tokens, (30, 130, 350, 220)))
    visible_packs = []
    if overlay:
        for t in in_region(tokens, (45, 208, 345, 512)):
            if re.fullmatch(r'\d+', t['text'].strip()):
                continue
            label = re.sub(r'\s+\d{1,4}$', '', t['text']).strip()
            visible_packs.append({'text': label, 'evidence': t})
        for name in ['total_notes', 'bpm', 'artist']:
            fields[name] = value(reason='pack_list_occludes_metadata')
    return dict(fields=fields, visible_rows=[dict(title=r['title']['text'], game_level=int(r['level']['text']),
                duration_seconds=duration_seconds(r['time']['text']), highlight_score=r['highlight_score']) for r in rows],
                pack_list_visible=overlay, visible_packs=visible_packs, selected_pack_id=None,
                pack_reason='visible_list_does_not_prove_selected_pack' if overlay else 'pack_not_visible')


def result_context(tokens, player_name):
    names = []
    for t in in_region(tokens, (115, 225, 300, 630)):
        _, y = center(t)
        if any(re.fullmatch(r'\d+', n['text'].strip()) for n in in_region(tokens, (330, y-13, 400, y+13))):
            names.append(t)
    local = [t for t in names if compact(t['text']) == compact(player_name)]
    status = 'unknown'
    marker = []
    if len(local) == 1:
        _, y = center(local[0])
        marker = [t for t in in_region(tokens, (0, y-35, 58, y+35))
                  if compact(t['text']) in ('GAME', 'OVER', 'GAMEOVER')]
        # OVER is sufficient only inside the fixed GAME OVER badge region of this result skin.
        status = 'failed' if any('OVER' in compact(t['text']) for t in marker) else 'completed_observed'
    multi = len(names) > 1
    return dict(multiplayer=multi, player_name=player_name, local_player_status=status,
                local_row_evidence=local, gameover_evidence=marker,
                result_player_names=[t['text'] for t in names],
                footer_level_scope='host_only_not_local_chart' if multi else 'local_result_display',
                generates_play_record=False)


def room_context(tokens, player_name):
    roster = in_region(tokens, (20, 100, 995, 280))
    local = [t for t in roster if compact(t['text']) == compact(player_name)]
    badges = [t for t in roster if compact(t['text']) in ('HOST', 'READY')]
    is_host = None
    if len(local) == 1:
        x, y = center(local[0])
        hosts = [t for t in badges if compact(t['text']) == 'HOST']
        if hosts:
            is_host = any(abs(center(t)[0]-x) < 75 and y-100 < center(t)[1] < y for t in hosts)
    return dict(local_is_host=is_host,
                role='host' if is_host else ('participant' if is_host is False else
                     ('participant_host_unknown' if local else 'observer_or_unconfirmed')),
                multiplayer=None, reason='room_capacity_is_not_active_player_count')
