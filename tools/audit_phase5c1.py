"""Read-only legacy MAX/count audit. No migrations, rescoring or best updates."""
from collections import defaultdict
from contextlib import closing
from datetime import datetime,timezone
import json
from pathlib import Path
import sqlite3

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'reports/phase5c1'


def audit_evidence():
    rows=[];groups=defaultdict(list)
    with closing(sqlite3.connect((OUT/'baseline.sqlite3').as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        for record in db.execute('SELECT r.*,d.song_id,d.difficulty,s.saved_title FROM play_records r LEFT JOIN game_difficulties d ON d.difficulty_id=r.difficulty_id LEFT JOIN songs s ON s.song_id=d.song_id ORDER BY record_id'):
            r=dict(record);e=json.loads(r.pop('evidence_json'));v=e.get('values') or {}
            catalog=(e.get('difficulty') or {}).get('total_notes')
            if catalog is None:catalog=(e.get('note_sources') or {}).get('database_total_notes')
            if catalog is None and e.get('notes_verified'):catalog=v.get('total_notes')
            numbers=[r[k] for k in ('perfect','cool','bad','miss')]
            total=sum(numbers) if all(type(n) is int for n in numbers) else None
            entry=dict(record_id=r['record_id'],session_id=r['session_id'],song_id=r['song_id'],difficulty=r['difficulty'],
                title=r['saved_title'],completion=r['completion_status'],judgments=dict(zip(('perfect','cool','bad','miss'),numbers)),
                catalog_total_notes=catalog,judged_total=total,stored_total_notes=r['total_notes'],stored_max=r['max_score'],
                stored_ex=r['ex_score'],stored_lamp=r['lamp_status'],
                difference=total-catalog if total is not None and catalog is not None else None,
                max_basis_audit='legacy_definition_not_independently_verified',changed=False,
                played_at=r['played_at'],confirmed_at=r['confirmed_at'])
            rows.append(entry);groups[(r['song_id'],r['difficulty'])].append(entry)
    observations=[]
    for path in (ROOT/'data/monitor/sessions').glob('*/result-review.json'):
        row=json.loads(path.read_text(encoding='utf8'));s=row.get('locked_session') or {};selection=s.get('selection') or {}
        fields=row['ocr']['fields'];nums=[fields[k].get('value') for k in ('perfect','cool','bad','miss')]
        total=sum(nums) if all(type(n) is int for n in nums) else None
        observations.append(dict(session_id=row['session_id'],title=fields['title']['value'],
            completion=fields['completion_status']['value'],judged_total=total,
            selected_total_notes=(selection.get('ocr_fields') or selection.get('fields') or {}).get('total_notes',{}).get('value'),
            source_type=selection.get('source_type'),song_id=(row.get('draft') or {}).get('song_id') or s.get('song_id'),
            judgments=dict(zip(('perfect','cool','bad','miss'),nums)),
            notes='One observation per Session. Draft/Record reuse is not another play.'))
    repeated=[]
    for (sid,diff),items in groups.items():
        completed=[i for i in items if i['completion']=='COMPLETED']
        if len(completed)>1:repeated.append(dict(song_id=sid,difficulty=diff,sessions=[i['session_id'] for i in completed],judged_totals=[i['judged_total'] for i in completed]))
    result=dict(as_of=datetime.now(timezone.utc).isoformat(),schema=7,legacy_records=rows,
        observations=observations,repeated_confirmed_chart_plays=repeated,
        hypotheses=[dict(hypothesis='CSV counts all chart objects',status='unverified; no modified game counting source provided'),
                    dict(hypothesis='result judgments count separate runtime events',status='unverified; UI counters alone do not define the event model'),
                    dict(hypothesis='special notes cause differences',status='unverified; no controlled note-type experiment'),
                    dict(hypothesis='same chart judged_total stable across full completions',status='insufficient independent repeated plays'),
                    dict(hypothesis='AC/ACP guarantees equal catalog count',status='unverified; equal examples cannot prove a universal rule')],
        no_record_or_max_recalculated=True)
    (OUT/'legacy-audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    text=['# 舊成績與遊戲計數唯讀稽核','',f'開工快照：{len(rows)} 筆正式成績，Schema 7。所有儲存值保持不變。','',
          '| Record | Song / 難度 | Catalog | 判定合計 | 差異 | 舊 MAX | 舊燈號 |','|---|---|---:|---:|---:|---:|---|']
    for r in rows:text.append(f"| {r['record_id']} | {r['title']} / {r['difficulty']} | {r['catalog_total_notes']} | {r['judged_total']} | {r['difference']} | {r['stored_max']} | {r['stored_lamp']} |")
    text+=['','這些舊 MAX 依歷史規則保存，未找到獨立的遊戲 MAX 定義證據；不重算、不更動歷史最佳。',
        '',f'保存的原始結算 OCR 有 {len(observations)} 個獨立 Session；同一 Session 的草稿與正式紀錄不重複計為多次遊玩。',
        'Heart of Witch 已知 Catalog1194、判定1192；缺少當次選歌截圖，不宣稱選歌 OCR 已驗證1194。',
        '', '目前無法證明 CSV 是全部物件數、判定是事件數，或特殊音符造成差異。也不能從「差2」推出通用規則。',
        '', '需要的實測：同一 Song ID／EASY-NORMAL-HARD／LV 多次完整遊玩；每次保留選歌與結算原圖、時間、模式及本人列。至少包含有 Bad/Miss、AC、ACP 與 GAME OVER；後者不可用部分判定當完整數。',
        '若能提供修改版的音符計數／結算程式或明確規則說明，才可驗證物件、判定事件及特殊音符的關聯。未取得前，兩種 MAX 都只是假設預覽。']
    (OUT/'legacy-audit.md').write_text('\n'.join(text),encoding='utf8')
    print(dict(legacy_records=len(rows),session_observations=len(observations),repeated_chart_groups=len(repeated)))


if __name__=='__main__':audit_evidence()
