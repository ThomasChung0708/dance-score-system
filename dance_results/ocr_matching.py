"""Read-only candidate retrieval; search hits never imply a score binding."""
from __future__ import annotations

import json
import sqlite3
import unicodedata
from pathlib import Path


def normalize(value):
    return ' '.join(unicodedata.normalize('NFKC', value or '').casefold().split())


class ReadOnlyCatalog:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA query_only=ON')
        self.packs = [dict(r) for r in self.db.execute('SELECT * FROM packs ORDER BY pack_id')]
        self.songs = {r['song_id']: dict(r) for r in self.db.execute('''
            SELECT s.*,p.name AS pack_name FROM songs s LEFT JOIN packs p ON p.pack_id=s.pack_id''')}
        self.index = {}
        self.reviews = set()
        for song in self.songs.values():
            for name in [song['saved_title'], song['latest_title']]:
                self._index(name, song['song_id'])
        for r in self.db.execute('SELECT song_id,saved_title,payload_json FROM song_sources'):
            self._index(r['saved_title'], r['song_id'])
            data = json.loads(r['payload_json'])
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

    def close(self):
        self.db.close()

    def match_official(self, fields, *, source_confirmed=False):
        """K catalog assistance requires an explicit Official source and chart.

        A title search alone never binds. Missing optional screen notes can be
        filled from the catalog, but observed notes must agree, even at low OCR
        confidence. Unknown metadata on a namesake cannot eliminate it.
        """
        def reliable(key):
            item = fields.get(key, {})
            return item.get('value') is not None and item.get('confidence', 0) >= .9 and not item.get('reason')
        ids = sorted(i for i in self.index.get(normalize(fields.get('title', {}).get('value')), set())
                     if self.songs[i]['source_type'] == 'official')
        candidates = []; eligible = []; uncertain = False
        difficulty = fields.get('difficulty', {}).get('value')
        for sid in ids:
            gd = self.difficulties.get((sid, difficulty)); src = self.official_sources.get(sid)
            missing = []; conflicts = []
            if not src or src['mode'] != 'K':missing.append('official_k_source')
            if not gd:missing.append('catalog_game_difficulty')
            elif gd['game_level'] is None or not gd['level_source'] or not gd['notes_source'] or not gd['notes_verified'] or gd['total_notes'] is None:
                missing.append('verified_catalog_chart')
            else:
                for key in ('game_level', 'total_notes'):
                    observed = fields.get(key, {}).get('value')
                    if observed is not None and observed != gd[key]:conflicts.append(key)
                bpm = fields.get('bpm', {}).get('value')
                if bpm is not None and src:
                    from decimal import Decimal, InvalidOperation
                    try:
                        if Decimal(str(bpm)) != Decimal(src['bpm_raw']):conflicts.append('bpm')
                    except (InvalidOperation, ValueError):conflicts.append('bpm')
            if missing:uncertain = True
            entry = dict(song_id=sid, difficulty_id=gd['difficulty_id'] if gd else None,
                         missing=missing, conflicts=conflicts)
            candidates.append(entry)
            if not missing and not conflicts:eligible.append(entry)
        level=fields.get('game_level',{}).get('value')
        required = all(reliable(k) for k in ('title', 'difficulty', 'game_level')) and (
            difficulty in ('EASY','NORMAL','HARD') and type(level) is int and 0<level<=10**7)
        # A supplied but unreadable optional observation is a conflict to review,
        # rather than permission to replace pixels with catalog values.
        optional_ok = all(fields.get(k, {}).get('value') is None or reliable(k) for k in ('total_notes','bpm'))
        notes=fields.get('total_notes',{}).get('value')
        optional_ok=optional_ok and (notes is None or type(notes) is int and 0<notes<=10**7)
        ok = source_confirmed is True and required and optional_ok and not uncertain and len(eligible) == 1
        chosen = eligible[0] if ok else None
        return dict(status='confirmed_official_catalog' if ok else 'pending_manual_review',
                    confirmed_song_id=chosen['song_id'] if chosen else None,
                    bound_song_id=chosen['song_id'] if chosen else None,
                    bound_difficulty_id=chosen['difficulty_id'] if chosen else None,
                    candidate_count=len(candidates), candidates=candidates,
                    reasons=[] if ok else ['official_source_chart_evidence_missing_ambiguous_or_conflicting'],
                    method='official_k_title_difficulty_level_and_observed_metadata')

    def visible_pack_candidates(self, text):
        """A clipped label may suggest a Pack; it does not confirm selection."""
        key = normalize(text)
        return [dict(pack_id=p['pack_id'], root_id=p['root_id'], name=p['name'])
                for p in self.packs if key and (normalize(p['name']) == key or
                    (len(key) >= 12 and normalize(p['name']).startswith(key)))]

    def match(self, fields, confirmed_pack_id=None, source_type=None):
        title = fields.get('title', {}).get('value')
        difficulty = fields.get('difficulty', {}).get('value')
        # Retrieve all exact-normalized titles, including raw source Title without Subtitle.
        ids = sorted(self.index.get(normalize(title), set()))
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
                    elif fields.get(name, {}).get('value') is not None and gd[name] != fields[name]['value']:
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

    def match_identity(self, fields, pack_id=None, source_type=None):
        """Bind one consistent identity; missing chart verification still gates auto.

        Unknown chart metadata cannot eliminate a namesake. A Pack constraint is
        applied only after comparing all title candidates, so a contradiction
        never silently selects a song from another Pack.
        """
        from .ocr_policy import AUTO_CONFIDENCE, SELECTION_REQUIRED

        match = self.match(fields, source_type=source_type)
        consistent = []
        uncertain_conflict = False
        for candidate in match['candidates']:
            gd = candidate.get('difficulty') or {}
            conflicts = set(candidate['conflicts']) - {'duration_seconds'}
            for key in ('game_level', 'total_notes', 'duration_seconds'):
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
        reliable = all(fields.get(k, {}).get('value') is not None
                       and fields[k].get('confidence', 0) >= AUTO_CONFIDENCE
                       and not fields[k].get('reason') for k in SELECTION_REQUIRED)
        level = fields.get('game_level', {}).get('value')
        reliable = (reliable and fields.get('difficulty', {}).get('value') in ('EASY', 'NORMAL', 'HARD')
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
        return match
