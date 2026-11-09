"""Phase 4A static PNG OCR CLI. No monitoring, migrations, or database writes."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import sqlite3
import sys
import time

from .ocr_engine import LocalOCREngine, cached_page
from .ocr_matching import ReadOnlyCatalog
from .ocr_screens import classify, parse_selection, result_context, room_context
from .sqlite_safety import audit, check_integrity

PROJECT = Path(__file__).resolve().parent.parent


def normalize_tokens(tokens, width, height):
    return [dict(t, box=[[x*1024/width, y*800/height] for x, y in t['box']]) for t in tokens]


def evaluate(result, labels):
    """Labels are evaluation-only, read AFTER OCR and matching. SHA must match."""
    expected = labels.get(result['image']['name'])
    if not expected:
        return {'status': 'unlabelled', 'accuracy': None}
    if expected['sha256'] != result['image']['sha256']:
        return {'status': 'image_hash_mismatch', 'accuracy': None}
    comparisons = [{'field': 'screen_type', 'expected': expected['screen_type'],
                    'actual': result['screen_type'], 'correct': expected['screen_type'] == result['screen_type']}]
    for key, wanted in expected.get('fields', {}).items():
        actual = (result.get('selection') or {}).get('fields', {}).get(key, {}).get('value')
        comparisons.append(dict(field=key, expected=wanted, actual=actual, correct=wanted == actual))
    if 'local_player_status' in expected:
        actual = result.get('context', {}).get('local_player_status')
        comparisons.append(dict(field='local_player_status', expected=expected['local_player_status'],
                                actual=actual, correct=actual == expected['local_player_status']))
    return dict(status='evaluated', correct=sum(c['correct'] for c in comparisons),
                total=len(comparisons), accuracy=sum(c['correct'] for c in comparisons)/len(comparisons),
                comparisons=comparisons)


def summarize(results):
    comparisons = [c for r in results for c in r.get('evaluation', {}).get('comparisons', [])]
    groups = {
        'screen_classification': [c for c in comparisons if c['field'] == 'screen_type'],
        'visible_selection_fields': [c for c in comparisons if c['field'] not in
                                     ('screen_type', 'local_player_status') and c['expected'] is not None],
        'occluded_fields_kept_null': [c for c in comparisons if c['field'] not in
                                     ('screen_type', 'local_player_status') and c['expected'] is None],
        'local_results_status': [c for c in comparisons if c['field'] == 'local_player_status'],
    }
    return dict(image_count=len(results), screen_types=dict(Counter(r['screen_type'] for r in results)),
                metrics={name: {'correct': sum(c['correct'] for c in entries), 'total': len(entries),
                         'accuracy': sum(c['correct'] for c in entries)/len(entries) if entries else None}
                         for name, entries in groups.items()},
                accuracy_definition='exact match against separately inspected labels; model confidence is not accuracy',
                automatic_bindings=0, pending_selection_matches=sum(r['screen_type'] == 'song_select' for r in results))


def markdown_report(report):
    lines = ['# Phase 4A 靜態畫面 OCR 實測', '',
             f"處理 {report['summary']['image_count']} 張原始 PNG；正式 SQLite 唯讀、沒有 migration、沒有成績寫入。", '',
             '## 實測指標', '', '| 項目 | 正確／總數 | 準確率 |', '|---|---:|---:|']
    labels = {'screen_classification': '畫面分類', 'visible_selection_fields': '可見選歌欄位（完全相同）',
              'occluded_fields_kept_null': '遮蔽欄位保持 NULL', 'local_results_status': '本人結算狀態'}
    for name, metric in report['summary']['metrics'].items():
        percent = f"{metric['accuracy']:.1%}" if metric['accuracy'] is not None else '未標註'
        lines.append(f"| {labels[name]} | {metric['correct']}／{metric['total']} | {percent} |")
    lines += ['', '準確率來自人工逐圖標註的完全相同比對；不是 OCR 模型信心值。這是開發樣本結果，並非未知畫面的泛化準確率。逐圖比率僅涵蓋已標註欄位，不代表全畫面文字正確率；Pack 原文與補全候選未納入選歌欄位分母。', '',
              '## 逐圖結果', '', '| 圖片 | 類型 | 選歌資料／狀態 | 候選 Song ID | 逐圖正確率 | 失敗／待確認原因 |',
              '|---|---|---|---|---:|---|']
    for r in report['images']:
        selection = r.get('selection')
        text = r.get('context', {}).get('local_player_status', '非選歌畫面，不擷取被遮擋的歌曲設定')
        if selection:
            f = selection['fields']; v = lambda k: f[k]['value'] if f[k]['value'] is not None else '未取得'
            text = f"{v('title')} / {v('difficulty')} / LV {v('game_level')} / {v('total_notes')} notes / BPM {v('bpm')} / {v('duration_seconds')} 秒"
        candidates = ', '.join(str(c['song_id']) for c in r.get('matching', {}).get('candidates', [])) or '—'
        e = r.get('evaluation', {}); accuracy = f"{e['accuracy']:.1%} ({e['correct']}/{e['total']})" if e.get('accuracy') is not None else '未標註'
        esc = lambda x: str(x).replace('|', '\\|').replace('\n', ' ')
        lines.append('| '+' | '.join(map(esc, [r['image']['name'], r['screen_type'], text, candidates,
                                                accuracy, '；'.join(r['issues']) or '無 OCR 欄位錯誤']))+' |')
    lines += ['', '## Pack 清單與候選路徑', '']
    for r in report['images']:
        selection = r.get('selection')
        if not selection:
            continue
        lines += ['### '+r['image']['name'], '']
        lines.append('可見 Pack 文字：'+('；'.join(p['text'] for p in selection['visible_packs']) or '未顯示'))
        lines.append('')
        for c in r['matching']['candidates']:
            lines.append(f"- Song {c['song_id']}／Pack {c['pack_id']}：{c['relative_path']}；缺少：{', '.join(c['missing'])}")
        lines.append('')
    lines += ['', '## Pack 與 JOINT', '',
              '黑色清單顯示的是多個可選 Pack，不能把可見列當成目前已選 Pack。截斷名稱只列出資料庫候選；沒有人工 Pack 證據時 selected_pack_id 保持 NULL。', '',
              'JOINT 的 HARD LV 17／821／96 秒與 HARD LV 20／929／95 秒由畫面獨立擷取；不以檔名、Artist 或候選排序選 Song。正式庫的 Song 990、1004 尚缺遊戲難度與已確認時間，均維持待人工確認。', '',
              '可用 --pack-id 明確指定已人工確認的 Pack ID 作唯讀候選篩選；這只補 Pack 證據，不補缺失 LV／音符／時間。每張圖片獨立處理，不從其他圖片偷偷沿用 Pack。', '',
              '## 限制與保護', '',
              '- 靜態原型針對這批約 1024×800、含視窗標題列的遊戲皮膚；分類依 OCR 錨點，選歌欄位依固定相對區域與可見高亮。其他皮膚／裁切可能回傳 unknown 或缺值，尚未保證適用。',
              '- 不可見的 BPM、音符數、Pack 不猜測；未確認的時間只留在 JSON，不寫入資料庫。',
              '- 多人結算按設定暱稱定位本人列；他人的 GAME OVER 不改變 TC 的結果。多人底部歌曲 LV 是 HOST 所選譜面等級，不能直接套用到玩家自選的另一個難度；不解析人物等級。',
              '- 遊戲中血條空白或 MISS 不能單憑此畫面宣告最終 GAME OVER；原型不產生成績、燈號或 EX Score。',
              '- 支援多語言的模型已實際辨識日文 Artist「川田まみ」、中文 UI／Pack「osu音樂」與英文歌名。這批選歌截圖沒有中文或日文的已選主歌名，尚不能聲稱已實測所有語系歌名。',
              '- 正式 schema 5 沒有 verified duration 欄位；即使查到唯一歌名仍禁止自動綁定。官方 CSV 的 dur* 不作遊戲時間替代。',
              '- 完整 OCR 文本與座標保存在本機 JSON，可能包含畫面中的帳號設定或聊天；沒有上傳圖片至 OCR 服務。', '',
              '## 唯讀驗證', '',
              f"- 資料表逐表稽核一致：{report['safety']['database_unchanged']}",
              f"- 原始 PNG SHA-256 一致：{report['safety']['original_images_unchanged']}",
              '- SQLite 完整性與外鍵檢查通過。', '',
              '## 引擎來源與相容性', '',
              'CPython 3.11.5 x64／Windows 10 實際安裝並執行 ONNX CPU；依賴僅安裝在 .venv-ocr。模型首次使用下載到 .ocr-models，後續本機執行。', '',
              '- [RapidOCR 的 Python 版本資訊](https://pypi.org/project/rapidocr/3.10.0/)',
              '- [ONNX Runtime Windows wheels](https://pypi.org/project/onnxruntime/1.24.1/#files)',
              '- [RapidOCR PP-OCRv5 設定](https://rapidai.github.io/RapidOCRDocs/main/en/install_usage/rapidocr/how_to_use_ppocrv5/)',
              '- [PaddleOCR PP-OCRv5 多語言能力](https://paddlepaddle.github.io/PaddleOCR/v3.0.0/en/version3.x/algorithm/PP-OCRv5/PP-OCRv5.html)', '']
    return '\n'.join(lines)


def html_report(report, markdown):
    esc = lambda text: html.escape(str(text), quote=True)
    cards = []
    for name, metric in report['summary']['metrics'].items():
        rate = f"{metric['accuracy']:.1%}" if metric['accuracy'] is not None else '未標註'
        cards.append(f'<div class="metric"><b>{rate}</b><br>{esc(name)}<br>{metric["correct"]} / {metric["total"]}</div>')
    rows = []
    for r in report['images']:
        selection = r.get('selection'); fields = []
        if selection:
            for key, item in selection['fields'].items():
                shown = '未取得' if item['value'] is None else item['value']
                fields.append(f'<div><span>{esc(key)}</span> <b>{esc(shown)}</b></div>')
        else:
            fields.append(esc(r.get('context', {}).get('local_player_status', '非選歌畫面')))
        candidates = ''.join(f'<li>Song {c["song_id"]} / Pack {c["pack_id"]}<br>{esc(c["relative_path"])}</li>'
                             for c in r.get('matching', {}).get('candidates', []))
        packs = '；'.join(p['text'] for p in (selection or {}).get('visible_packs', []))
        notes = '<br>'.join(esc(i) for i in r['issues'])
        e = r.get('evaluation', {})
        accuracy = f"{e['accuracy']:.1%} ({e['correct']}/{e['total']})" if e.get('accuracy') is not None else '未標註'
        rows.append(f'<tr><td><a href="{esc(Path(r["image"]["path"]).as_uri())}">'
                    f'<img loading="lazy" alt="{esc(r["image"]["name"])}" src="{esc(Path(r["image"]["path"]).as_uri())}"></a>'
                    f'<br><b>{esc(r["image"]["name"])}</b><br><small>{esc(r["screen_type"])}</small></td>'
                    f'<td>{"".join(fields)}<p>逐圖正確率：{accuracy}</p></td>'
                    f'<td><ul>{candidates or "—"}</ul>{("<p>可見 Pack（未確認選取）："+esc(packs)+"</p>") if packs else ""}'
                    f'<details><summary>缺值／待確認原因</summary>{notes or "無欄位錯誤"}</details></td></tr>')
    return ('<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            '<title>Phase 4A 靜態 OCR 實測</title><style>'
            'body{background:#17121f;color:#eee8f7;font:15px/1.65 "Segoe UI","Microsoft JhengHei",sans-serif;margin:28px}'
            'h1{font-size:27px}a{color:#cba5ff}.metrics{display:flex;gap:14px;flex-wrap:wrap}.metric{padding:16px;background:#2c203e;border-radius:12px}'
            '.metric b{font-size:28px;color:#cba5ff}table{border-collapse:collapse;width:100%;margin:25px 0}'
            'th,td{border-bottom:1px solid #493853;padding:15px;vertical-align:top;text-align:left}th{background:#30213f}'
            'td:first-child{width:220px}td:nth-child(2){min-width:220px}img{width:210px;height:auto;border-radius:6px}'
            'span,small{color:#b7a8c7}ul{padding-left:18px}li{margin-bottom:10px;overflow-wrap:anywhere}'
            'summary{color:#cba5ff;cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.6 monospace}'
            '</style><h1>Phase 4A · 靜態遊戲畫面 OCR</h1>'
            '<p>原圖唯讀 · 正式 SQLite 唯讀 · 只輸出候選 · 沒有即時監控</p>'
            '<p>以下為人工逐圖標註比對的開發樣本結果，不代表未知畫面泛化準確率。未取得必要證據時保持待人工確認。</p>'
            '<div class="metrics">'+''.join(cards)+'</div><table><thead><tr><th>原始圖片／類型</th><th>辨識資料</th>'
            '<th>候選與原因</th></tr></thead><tbody>'+''.join(rows)+'</tbody></table>'
            '<details><summary>完整文字報告與限制</summary><pre>'+esc(markdown)+'</pre></details></html>')


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='Phase 4A：唯讀 SQLite 的本機靜態 PNG OCR')
    parser.add_argument('--input', type=Path, default=PROJECT/'tests'/'專案測試圖片')
    parser.add_argument('--database', type=Path, default=PROJECT/'data'/'library.sqlite3')
    parser.add_argument('--output', type=Path, default=PROJECT/'reports'/'phase4a')
    parser.add_argument('--models', type=Path, default=PROJECT/'.ocr-models')
    parser.add_argument('--labels', type=Path, help='僅供辨識後計算準確率，絕不送入 OCR／匹配器')
    parser.add_argument('--player-name', default='TC')
    parser.add_argument('--pack-id', type=int, help='人工確認的來源 Pack；套用到本批圖片，混合 Pack 請分開執行')
    parser.add_argument('--no-cache', action='store_true', help='重新執行每張圖片的模型推論，不重用辨識快取')
    args = parser.parse_args(argv)
    catalog = None
    try:
        from PIL import Image
        source = args.input.resolve(strict=True)
        files = sorted([source] if source.is_file() and source.suffix.lower() == '.png' else
                       [p for p in source.rglob('*') if p.suffix.lower() == '.png'])
        if not files:
            raise ValueError('找不到 PNG 圖片。')
        if args.output.resolve() == source or (source.is_dir() and args.output.resolve().is_relative_to(source)):
            raise ValueError('報告位置不可位於原始圖片資料夾內。')
        for directory in [args.output, args.models]:
            if directory.resolve() == source or (source.is_dir() and directory.resolve().is_relative_to(source)):
                raise ValueError('輸出／模型資料夾不可覆蓋原始圖片來源。')
            directory.mkdir(parents=True, exist_ok=True)
        catalog = ReadOnlyCatalog(args.database)
        if args.pack_id is not None and args.pack_id not in {p['pack_id'] for p in catalog.packs}:
            raise ValueError('Pack ID 不存在。')
        before = audit(catalog.db); check_integrity(catalog.db)
        originals = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
        engine = LocalOCREngine(args.models)
        results = []
        for path in files:
            start = time.monotonic()
            result = dict(image={'name': path.name, 'path': str(path), 'sha256': originals[path]}, issues=[])
            try:
                with Image.open(path) as im:
                    result['image']['size'] = list(im.size)
                    native_tokens = engine.recognize(im) if args.no_cache else cached_page(engine, im, originals[path], args.output/'cache')
                    tokens = normalize_tokens(native_tokens, *im.size)
                    screen_type, evidence = classify(tokens)
                    result.update(screen_type=screen_type, classification_evidence=evidence,
                                  raw_ocr=native_tokens, selection=None)
                    image = im.convert('RGB').resize((1024, 800))
                    if screen_type == 'song_select':
                        selection = parse_selection(tokens, image)
                        for p in selection['visible_packs']:
                            p['catalog_candidates'] = catalog.visible_pack_candidates(p['text'])
                        selection['selected_pack_id'] = args.pack_id
                        if args.pack_id is not None:
                            selection['pack_reason'] = 'explicit_manual_cli_context'
                        result['selection'] = selection
                        result['matching'] = catalog.match(selection['fields'], args.pack_id)
                        result['issues'] += [k+': '+str(v['reason']) for k, v in selection['fields'].items() if v['value'] is None]
                        result['issues'] += result['matching']['reasons']
                    elif screen_type == 'results':
                        # A narrow enlarged badge crop improves recall; original PNG remains untouched.
                        crop = im.crop((0, int(im.height*.27), int(im.width*.062), int(im.height*.79)))
                        extra = engine.recognize(crop.resize((crop.width*3, crop.height*3)),
                                                 origin=(0, int(im.height*.27)), scale=3)
                        result['gameover_crop_ocr'] = extra
                        result['context'] = result_context(tokens+normalize_tokens(extra, *im.size), args.player_name)
                        result['issues'].append('result_only_no_selected_pack_or_complete_chart_evidence')
                    elif screen_type == 'waiting_room':
                        result['context'] = room_context(tokens, args.player_name)
                        result['issues'].append('room_preview_not_a_complete_song_selection')
                    elif screen_type == 'gameplay':
                        result['context'] = dict(local_player_status='in_progress_not_final', generates_play_record=False)
                        result['issues'].append('gameplay_is_not_a_final_result_or_selection')
                    elif screen_type == 'unknown':
                        result['issues'].append('unsupported_or_unreadable_screen')
            except Exception as error:
                result.update(screen_type='error', error=f'{type(error).__name__}: {error}')
                result['issues'].append('ocr_processing_failed')
            result['processing_seconds'] = round(time.monotonic()-start, 3)
            results.append(result)
            print(f"{path.name}: {result['screen_type']} ({result['processing_seconds']}s)", flush=True)
        # Ground truth can only affect evaluation, after every recognition/matching result exists.
        labels = json.loads(args.labels.read_text(encoding='utf-8'))['images'] if args.labels else {}
        for result in results:
            result['evaluation'] = evaluate(result, labels)
        after = audit(catalog.db); check_integrity(catalog.db)
        safety = dict(database_unchanged=before == after,
                      original_images_unchanged=all(hashlib.sha256(p.read_bytes()).hexdigest() == h for p, h in originals.items()),
                      database_before=before, database_after=after, database_mode='mode=ro + query_only=ON')
        model_files = [dict(name=p.name, bytes=p.stat().st_size, sha256=hashlib.sha256(p.read_bytes()).hexdigest())
                       for p in args.models.glob('*.onnx')]
        report = dict(phase='4A', created_at=datetime.now(timezone.utc).isoformat(),
                      engine=engine.metadata(), models=model_files,
                      coordinate_space='raw_ocr: original pixels; field evidence: normalized 1024x800',
                      manual_pack_context=args.pack_id, summary=summarize(results), images=results, safety=safety)
        (args.output/'results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        markdown = markdown_report(report)
        (args.output/'report.md').write_text(markdown, encoding='utf-8')
        # Local original thumbnails only; no remote scripts, images or network calls.
        (args.output/'report.html').write_text(html_report(report, markdown), encoding='utf-8')
        print(json.dumps(report['summary'], ensure_ascii=False, indent=2))
        print('報告：'+str((args.output/'report.html').resolve()))
        return 0 if safety['database_unchanged'] and safety['original_images_unchanged'] and not any(
            r['screen_type'] == 'error' for r in results) else 2
    except (OSError, ValueError, RuntimeError, ImportError, sqlite3.Error) as error:
        print('無法完成 OCR：'+str(error), file=sys.stderr)
        return 2
    finally:
        if catalog:
            catalog.close()


if __name__ == '__main__':
    raise SystemExit(main())
