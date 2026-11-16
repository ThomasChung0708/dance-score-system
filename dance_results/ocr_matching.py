"""Read-only candidate retrieval; search hits never imply a score binding."""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from pathlib import Path

from .result_consistency import screen_catalog


def normalize(value):
    return ' '.join(unicodedata.normalize('NFKC', value or '').casefold().split())


def same_song_title_key(value):
    """Space/tilde-insensitive retrieval and observed-title comparison key.

    Long vowels and hyphens remain distinct catalog symbols. Confusables and
    edit distance belong exclusively to probably_same_title, never this key.
    """
    key = ''.join(unicodedata.normalize('NFKC', value or '').casefold().split())
    return key.translate(str.maketrans({'〜': '~', '〰': '~', '∼': '~'}))


def probably_same_title(selected, preview):
    """Advisory PLAYING check only; never catalog retrieval or binding.

    Explicit visual groups: こ/ニ/に, ロ/口, カ/力, エ/工, ー/一/-,
    ヘ/へ, ベ/べ, ペ/ぺ, リ/り. One other edit needs >=6 characters
    on BOTH sides (at least five sixths of either title must survive).
    """
    left, right = same_song_title_key(selected), same_song_title_key(preview)
    groups = ('こニに', 'ロ口', 'カ力', 'エ工', 'ー一-', 'ヘへ', 'ベべ', 'ペぺ', 'リり')
    mapping = str.maketrans({char: group[0] for group in groups for char in group})
    a, b = left.translate(mapping), right.translate(mapping)
    previous = list(range(len(b) + 1))
    for i, char in enumerate(a, 1):
        current = [i]
        for j, other in enumerate(b, 1):
            current.append(min(current[-1] + 1, previous[j] + 1,
                               previous[j - 1] + (char != other)))
        previous = current
    distance = previous[-1]
    bound = 1 if min(len(a), len(b)) >= 6 else 0
    method = 'exact_key' if left == right else 'visual_confusables' if a == b else 'one_edit'
    return dict(same=bool(left and right and distance <= bound), method=method,
                selected_key=left, preview_key=right, selected_visual_key=a,
                preview_visual_key=b, distance=distance, edit_bound=bound)


def stylized_title_key(value, *, allow_latin=False):
    """Confusable key for pack-constrained Japanese/mixed title retrieval.

    Preserve unvoiced versus voiced kana; only the b/p marks are confusable.
    This is advisory identity evidence, never automatic title authority.
    """
    key = same_song_title_key(value)
    if not re.search(r'[\u3040-\u30ff\u3400-\u9fff]', key) and not (
            allow_latin and re.search(r'[a-z]', key) and re.fullmatch(r'[a-z0-9ı|\s~♡♥❤]+', key)):
        return None
    key = ''.join(chr(ord(c)-0x60) if '\u30a1' <= c <= '\u30f6' else c for c in key)
    key = unicodedata.normalize('NFC', unicodedata.normalize('NFD', key).replace('\u309a', '\u3099'))
    key = key.translate(str.maketrans({'♥': '♡', '❤': '♡'})).replace('\ufe0f', '')
    key = re.sub(r'\s*([♡~])\s*', r'\1', key)
    return key.translate(str.maketrans({'1': 'l', 'i': 'l', 'ı': 'l', '|': 'l'}))


class ReadOnlyCatalog:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA query_only=ON')
        self.packs = [dict(r) for r in self.db.execute('SELECT * FROM packs ORDER BY pack_id')]
        self.songs = {r['song_id']: dict(r) for r in self.db.execute('''
            SELECT s.*,p.name AS pack_name FROM songs s LEFT JOIN packs p ON p.pack_id=s.pack_id''')}
        self.index = {}
        self.retrieval_index = {}
        self.reviews = set()
        for song in self.songs.values():
            for name in [song['saved_title'], song['latest_title']]:
                self._index(name, song['song_id'])
        sources = list(self.db.execute('SELECT song_id,format,saved_title,saved_subtitle,payload_json FROM song_sources'))
        subtitles = {}
        for r in sources:
            if r['format'] == 'sm' and r['saved_subtitle']:
                subtitles.setdefault(r['song_id'], set()).add(r['saved_subtitle'])
        for r in sources:
            self._index(r['saved_title'], r['song_id'])
            data = json.loads(r['payload_json'])
            # TITLE is separate from SUBTITLE in SM. Strip a DWI suffix only
            # when the same song's source metadata explicitly identifies it.
            current_payload = same_song_title_key(data.get('title')) == same_song_title_key(r['saved_title'])
            titles = [data.get('title'), *data.get('title_fields', {}).get('TITLE', [])] if current_payload else []
            for title in titles:
                self._index(title, r['song_id'])
                if r['format'] == 'dwi' and title:
                    for subtitle in subtitles.get(r['song_id'], set()):
                        full, suffix = normalize(title), normalize(subtitle)
                        if suffix and full.endswith(' ' + suffix):
                            self._index(full[:-len(suffix)].rstrip(), r['song_id'])
            if data.get('name_review_required'):
                self.reviews.add(r['song_id'])
        for r in self.db.execute('SELECT song_id,display_title FROM song_title_aliases WHERE confirmed=1'):
            self._index(r['display_title'], r['song_id'])
        self.difficulties = {}
        for r in self.db.execute('SELECT * FROM game_difficulties'):
            self.difficulties[(r['song_id'], r['difficulty'])] = dict(r)
        self.official_sources={r['song_id']:dict(r) for r in self.db.execute(
            'SELECT song_id,file_id,gn,mode,catalog_key,bpm_raw FROM official_song_sources')}

    def _index(self, name, song_id):
        if normalize(name):
            self.index.setdefault(normalize(name), set()).add(song_id)
            self.retrieval_index.setdefault(same_song_title_key(name), set()).add(normalize(name))

    def title_ids(self, title, pack_id=None, source_type=None):
        key = same_song_title_key(title)
        index = getattr(self, 'retrieval_index', None)
        ids = ({sid for name in index.get(key, set()) for sid in self.index.get(name, set())} if index is not None else
               {sid for name, songs in self.index.items() if same_song_title_key(name) == key for sid in songs})
        ids = ids | self.index.get(normalize(title), set())
        return {sid for sid in ids
                if (pack_id is None or self.songs[sid]['pack_id'] == pack_id)
                and (source_type not in ('custom', 'official') or self.songs[sid]['source_type'] == source_type)}

    def close(self):
        self.db.close()

    def match_official(self, fields, *, source_confirmed=False):
        """Bind official K charts by title, difficulty and LV.

        Notes break fully observed ties; mutable notes and BPM do not reject a
        unique chart. Missing identity metadata cannot eliminate a namesake.
        """
        def reliable(key):
            item = fields.get(key, {})
            return item.get('value') is not None and item.get('confidence', 0) >= .9 and not item.get('reason')
        ids = sorted(i for i in self.title_ids(fields.get('title', {}).get('value'))
                     if self.songs[i]['source_type'] == 'official')
        candidates = []; eligible = []; uncertain = False
        difficulty = fields.get('difficulty', {}).get('value')
        for sid in ids:
            gd = self.difficulties.get((sid, difficulty)); src = self.official_sources.get(sid)
            missing = []; conflicts = []
            if not src or src['mode'] != 'K':missing.append('official_k_source')
            if not gd:missing.append('catalog_game_difficulty')
            elif gd['game_level'] is None or not gd['level_source']:
                missing.append('verified_catalog_chart')
            else:
                for key in ('game_level',):
                    observed = fields.get(key, {}).get('value')
                    if observed is not None and observed != gd[key]:conflicts.append(key)
            if missing:uncertain = True
            entry = dict(song_id=sid, difficulty_id=gd['difficulty_id'] if gd else None,
                         missing=missing, conflicts=conflicts)
            candidates.append(entry)
            if not missing and not conflicts:eligible.append(entry)
        level=fields.get('game_level',{}).get('value')
        required = all(reliable(k) for k in ('title', 'difficulty', 'game_level')) and (
            difficulty in ('EASY','NORMAL','HARD') and type(level) is int and 0<level<=10**7)
        # Notes only disambiguate otherwise identical official charts. BPM and
        # mutable chart counts never disqualify a unique title/difficulty/LV.
        if (len(eligible) > 1 and reliable('total_notes')
                and type(fields['total_notes']['value']) is int and fields['total_notes']['value'] > 0
                and all(self.difficulties[(c['song_id'], difficulty)].get('total_notes') is not None for c in eligible)):
            tied = [c for c in eligible if self.difficulties[
                (c['song_id'], difficulty)].get('total_notes') == fields['total_notes']['value']]
            if len(tied) == 1:
                eligible = tied
        ok = source_confirmed is True and required and not uncertain and len(eligible) == 1
        chosen = eligible[0] if ok else None
        return dict(status='confirmed_official_catalog' if ok else 'pending_manual_review',
                    confirmed_song_id=chosen['song_id'] if chosen else None,
                    bound_song_id=chosen['song_id'] if chosen else None,
                    bound_difficulty_id=chosen['difficulty_id'] if chosen else None,
                    candidate_count=len(candidates), candidates=candidates,
                    reasons=[] if ok else ['official_source_chart_evidence_missing_ambiguous_or_conflicting'],
                    method='official_title_difficulty_level_notes_tiebreaker')

    def visible_pack_candidates(self, text):
        """A clipped label may suggest a Pack; it does not confirm selection."""
        key = normalize(text)
        return [dict(pack_id=p['pack_id'], root_id=p['root_id'], name=p['name'])
                for p in self.packs if key and (normalize(p['name']) == key or
                    (len(key) >= 12 and normalize(p['name']).startswith(key)))]

    def match(self, fields, confirmed_pack_id=None, source_type=None, *, candidate_ids=None):
        title = fields.get('title', {}).get('value')
        difficulty = fields.get('difficulty', {}).get('value')
        # Retrieve all exact-normalized titles, including raw source Title without Subtitle.
        ids = sorted(self.title_ids(title) if candidate_ids is None else candidate_ids)
        if source_type in ('official', 'custom'):
            ids = [i for i in ids if self.songs[i]['source_type'] == source_type]
        if confirmed_pack_id is not None:
            ids = [i for i in ids if self.songs[i]['pack_id'] == confirmed_pack_id]
        candidates = []
        for song_id in ids:
            song = self.songs[song_id]
            gd = self.difficulties.get((song_id, difficulty))
            missing = []; conflicts = []
            if confirmed_pack_id is None:
                missing.append('confirmed_pack')
            if song_id in self.reviews:
                missing.append('reviewed_source_title')
            for name in ['title', 'difficulty', 'game_level', 'total_notes', 'duration_seconds']:
                item = fields.get(name, {})
                if item.get('value') is None or item.get('confidence', 0) < 0.9:
                    missing.append('reliable_ocr_'+name)
            if not gd:
                missing.append('verified_game_difficulty')
            else:
                for name, source in [('game_level', 'level_source'), ('total_notes', 'notes_source')]:
                    if gd[name] is None or gd[source] is None or (name == 'total_notes' and not gd['notes_verified']):
                        missing.append('verified_database_'+name)
                    elif (name != 'total_notes' or screen_catalog(gd)) and fields.get(name, {}).get('value') is not None and gd[name] != fields[name]['value']:
                        conflicts.append(name)
            # Schema 5 has no confirmed game-duration columns. Never substitute CSV dur* / audio length.
            if not gd or not gd.get('duration_verified') or gd.get('duration_seconds') is None:
                missing.append('verified_database_duration')
            elif fields.get('duration_seconds', {}).get('value') != gd['duration_seconds']:
                conflicts.append('duration_seconds')
            candidates.append(dict(song_id=song_id, root_id=song['root_id'], pack_id=song['pack_id'],
                pack_name=song['pack_name'], song_folder=song['folder_name'], relative_path=song['relative_folder'],
                title=song['saved_title'] or song['latest_title'], source_type=song['source_type'],
                difficulty=gd, official_source=self.official_sources.get(song_id),
                missing=sorted(set(missing)), conflicts=conflicts))
        eligible = [c for c in candidates if not c['missing'] and not c['conflicts']]
        # Conservative prototype: even future compatible fields only yield an advisory result, no writes.
        status = 'unique_verified_candidate' if len(eligible) == len(candidates) == 1 else 'pending_manual_review'
        reasons = []
        if not candidates:
            reasons.append('no_exact_title_candidate')
        if confirmed_pack_id is None:
            reasons.append('pack_not_confirmed')
        if len(candidates) > 1:
            reasons.append('multiple_song_candidates')
        if candidates and not eligible:
            reasons.append('required_evidence_missing_or_conflicting')
        if len(eligible) > 1:
            reasons.append('multiple_fully_matching_candidates')
        return dict(status=status, bound_song_id=None, bound_difficulty_id=None,
                    candidate_count=len(candidates), candidates=candidates, reasons=reasons)

    def match_identity(self, fields, pack_id=None, source_type=None, *, candidate_ids=None):
        """Bind one consistent identity without requiring library note verification.

        Unknown chart metadata cannot eliminate a namesake. A Pack constraint is
        applied only after comparing all title candidates, so a contradiction
        never silently selects a song from another Pack.
        """
        from .ocr_policy import AUTO_CONFIDENCE, SELECTION_REQUIRED

        match = self.match(fields, source_type=source_type, candidate_ids=candidate_ids)
        unknown_source = source_type not in ('official', 'custom') and pack_id is None
        if unknown_source:
            exact = self.exact_official_identity(fields, match)
            if exact is not None:
                return exact
        if (pack_id is None and len(match['candidates']) > 1
                and all(c['source_type'] == 'custom' for c in match['candidates'])
                and all(fields.get(k, {}).get('confidence', 0) >= AUTO_CONFIDENCE
                    and not fields[k].get('reason') for k in ('title', 'difficulty', 'game_level'))):
            exact = [c for c in match['candidates'] if c.get('difficulty')
                and all(c['difficulty'].get(k) == fields.get(k, {}).get('value')
                    for k in ('difficulty', 'game_level'))]
            if len(exact) == 1:
                match['candidates'] = exact
                match['candidate_count'] = 1
        fuzzy = False
        # Confusable matching never searches outside an independently confirmed
        # Pack, and still runs every known chart conflict check below. Unknown
        # chart metadata cannot eliminate a namesake to manufacture uniqueness.
        new_key_low_title = (fields.get('title', {}).get('confidence', 0) < AUTO_CONFIDENCE
                             and normalize(fields.get('title', {}).get('value')) not in self.index)
        if (not match['candidates'] or new_key_low_title) and pack_id is not None:
            key = stylized_title_key(fields.get('title', {}).get('value'), allow_latin=True)
            title_field = fields.get('title', {})
            chart_readable = all(fields.get(k, {}).get('value') is not None
                                 and fields[k].get('confidence', 0) >= AUTO_CONFIDENCE
                                 and not fields[k].get('reason') for k in ('difficulty', 'game_level'))
            if not re.search(r'[\u3040-\u30ff\u3400-\u9fff]', title_field.get('value') or ''):
                notes = fields.get('total_notes') or {}
                chart_readable = (chart_readable and type(notes.get('value')) is int and notes['value'] > 0
                                  and notes.get('confidence', 0) >= AUTO_CONFIDENCE and not notes.get('reason'))
            if key and chart_readable and title_field.get('confidence', 0) >= .8 and not title_field.get('reason'):
                ids = {sid for name, songs in self.index.items() if stylized_title_key(name, allow_latin=True) == key
                       for sid in songs if self.songs[sid]['pack_id'] == pack_id}
                match = self.match(fields, pack_id, source_type, candidate_ids=ids)
                fuzzy = bool(match['candidates'])
        consistent = []
        uncertain_conflict = False
        for candidate in match['candidates']:
            gd = candidate.get('difficulty') or {}
            conflicts = set(candidate['conflicts']) - {'duration_seconds'}
            for key in ('game_level', 'total_notes', 'duration_seconds'):
                if key == 'total_notes' and not screen_catalog(gd):
                    continue
                observed = fields.get(key, {}).get('value')
                known = gd.get(key)
                if observed is not None and known is not None and observed != known:
                    conflicts.add(key)
                    item = fields[key]
                    if item.get('confidence', 0) < AUTO_CONFIDENCE or item.get('reason'):
                        uncertain_conflict = True
            candidate['conflicts'] = sorted(conflicts)
            if not conflicts:
                consistent.append(candidate)
        constrained = [c for c in consistent if pack_id is None or c['pack_id'] == pack_id]
        if pack_id is not None:
            match['candidates'] = [c for c in match['candidates'] if c['pack_id'] == pack_id]
            match['candidate_count'] = len(match['candidates'])
        uncharted = (not fuzzy and pack_id is not None and source_type == 'custom'
            and len(constrained) == 1
            and not any(sid == constrained[0]['song_id'] for sid, _ in self.difficulties)
            and fields.get('difficulty', {}).get('value') is None)
        # With no chart rows, LV cannot disambiguate catalog candidates. Bind
        # only the exact unique title in a confirmed pack; result-side temporal
        # LV evidence remains mandatory for automatic storage.
        required = ('title',) if uncharted else SELECTION_REQUIRED
        reliable = all(fields.get(k, {}).get('value') is not None
                       and fields[k].get('confidence', 0) >= (.8 if fuzzy and k == 'title' else AUTO_CONFIDENCE)
                       and not fields[k].get('reason') for k in required)
        level = fields.get('game_level', {}).get('value')
        reliable = (reliable and (uncharted or fields.get('difficulty', {}).get('value') in ('EASY', 'NORMAL', 'HARD'))
                    and type(level) is int and 0 < level <= 10**7)
        if not reliable or uncertain_conflict or len(constrained) != 1:
            match['reasons'] = ['identity_missing_ambiguous_or_pack_conflicting']
            return match
        chosen = constrained[0]
        if 'reviewed_source_title' in chosen['missing']:
            match['reasons'] = ['reviewed_source_title_required']
            return match
        verified = not (set(chosen['missing']) - {'confirmed_pack'})
        match.update(status='unique_verified_candidate' if verified else 'unique_consistent_candidate',
                     confirmed_song_id=chosen['song_id'], bound_song_id=chosen['song_id'],
                     bound_difficulty_id=(chosen.get('difficulty') or {}).get('difficulty_id'),
                     reasons=[])
        if fuzzy:
            match.update(method='pack_constrained_stylized_title', review_only=True)
        return match

    def exact_official_identity(self, fields, match):
        """Title/difficulty/LV resolves uncharted custom title copies.

        A custom chart with the same identity still forces review. Result
        consistency and temporal storage checks remain independent of binding.
        """
        def exact(candidate):
            gd = candidate.get('difficulty') or {}
            return all(fields.get(k, {}).get('value') is not None
                       and fields[k].get('confidence', 0) >= .9
                       and not fields[k].get('reason')
                       and (k == 'title' or gd.get(k) == fields[k]['value'])
                       for k in ('title', 'difficulty', 'game_level'))

        # Difficulty is the chart's own label, not a numeric database field.
        exact_candidates = [c for c in match['candidates'] if exact(c)]
        officials = [c for c in exact_candidates if c['source_type'] == 'official']
        if not officials or any(c['source_type'] == 'custom' for c in exact_candidates):
            return None
        official = self.match_official(fields, source_confirmed=True)
        if official.get('confirmed_song_id') not in {c['song_id'] for c in officials}:
            return None
        match.update(status='confirmed_official_catalog', confirmed_song_id=official['confirmed_song_id'],
                     bound_song_id=official['confirmed_song_id'],
                     bound_difficulty_id=official['bound_difficulty_id'], reasons=[],
                     method='unknown_source_exact_official_chart', resolved_source_type='official')
        return match


def result_identity_fields(selection, ocr, playing=None):
    """Add original result/playing evidence only where selection has no value."""
    import copy
    fields = copy.deepcopy(selection.get('ocr_fields', selection.get('fields') or {}))
    raw = ocr.get('fields') or {}
    for key in ('title', 'game_level', 'total_notes'):
        if key == 'game_level' and ocr.get('multiplayer'):
            continue
        if fields.get(key, {}).get('value') is None and raw.get(key, {}).get('value') is not None:
            fields[key] = copy.deepcopy(raw[key])
    if fields.get('total_notes', {}).get('value') is None:
        notes = (playing or {}).get('total_notes')
        if isinstance(notes, dict) and notes.get('value') is not None:
            fields['total_notes'] = copy.deepcopy(notes)
        else:
            judgments = [raw.get(k) or {} for k in ('perfect', 'cool', 'bad', 'miss')]
            completion = raw.get('completion_status') or {}
            if (completion.get('value') == 'COMPLETED' and completion.get('confidence', 0) >= .9
                    and not completion.get('reason') and all(type(f.get('value')) is int
                    and f['value'] >= 0 and f.get('confidence', 0) >= .9 and not f.get('reason') for f in judgments)):
                fields['total_notes'] = dict(value=sum(f['value'] for f in judgments),
                    confidence=min(f['confidence'] for f in judgments), reason=None,
                    method='completed_result_judgment_sum')
    return fields
