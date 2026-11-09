from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path

from . import __version__
from .models import Diagnostic, GAME_DIFFICULTIES, ScanReport

STATUS = {"ok": "歌名已讀取", "partial": "歌名待核對", "failed": "未取得歌名"}


def esc(value: object | None) -> str:
    return html.escape("未知" if value is None else str(value), quote=True)


def issues_html(issues: list[Diagnostic]) -> str:
    if not issues:
        return ""
    return '<ul class="issues">' + "".join(
        f'<li><code>{esc(d.code)}</code> {esc(d.message)}'
        + (f' <span class="muted">{esc(d.location)}</span>' if d.location else "") + '</li>'
        for d in issues) + '</ul>'


def render_html(report: ScanReport) -> str:
    summary = report.to_dict()["summary"]
    sources = {source.path: source for source in report.files}
    rows = []
    for song in report.songs:
        details = []
        for path in song.source_paths:
            source = sources[path]
            details.append(f'<details class="source"><summary>{esc(Path(path).name)} · {STATUS[source.status]}</summary>'
                           f'<p class="path">{esc(path)}</p><p>歌名：{esc(source.title)}<br>副標題：{esc(source.subtitle)}<br>'
                           f'Artist：{esc(source.artist)}<br>編碼：{esc(source.encoding)}<br>欄位編碼：{esc(source.metadata_encodings)}<br>檔案 SHA-256：<code>{esc(source.sha256)}</code></p>'
                           + (f'<p>上次可確認 Artist：{esc(source.saved_artist)}</p>' if source.saved_artist and source.saved_artist != source.artist else '')
                           + ('<p class="candidates">上次完整掃描未找到此來源；保留既有資料。</p>' if source.is_present is False else '')
                           + issues_html(source.diagnostics) + '</details>')
        display_pack = song.pack_name or "未知 Pack"
        title = f'<strong>{esc(song.title) if song.title else "歌名待確認"}</strong>'
        title += f'<small class="path">{esc(song.song_folder)}</small>'
        if song.song_id is not None:
            title += f'<small>歌曲 ID：{song.song_id}</small>'
        if song.is_present is False:
            title += '<p class="candidates">上次完整掃描未找到歌名來源；資料保留中。</p>'
        if song.saved_title and song.saved_title != song.title:
            title += f'<p class="candidates">上次完整讀取歌名：{esc(song.saved_title)}（本次尚未確認）</p>'
        if song.title is None and song.title_candidates:
            title += '<p class="candidates">來源歌名：<br>' + '<br>'.join(esc(x) for x in song.title_candidates) + '</p>'
        title += issues_html(song.diagnostics)
        search = " ".join([song.song_folder, display_pack, song.saved_title or "", *song.title_candidates, *song.source_paths])
        rows.append(f'<tr data-search="{esc(search)}" data-pack="{esc(song.storage_group or "")}" data-status="{song.status}">'
                    f'<td>{title}</td><td>{esc(display_pack)}</td>'
                    f'<td><span class="labels">{" / ".join(GAME_DIFFICULTIES)}</span><small>實際存在的難度待遊戲確認</small></td>'
                    '<td class="unknown">LV：待畫面擷取<br>總顆數：待遊戲確認</td>'
                    f'<td><span class="badge {song.status}">{STATUS[song.status]}</span><small>{len(song.source_paths)} 份歌名來源</small>'
                    + ''.join(details) + '</td></tr>')
    packs = sorted({f.storage_group for f in report.files if f.storage_group})
    options = "".join(f'<option value="{esc(pack)}">{esc(pack)}</option>' for pack in packs)
    counts = summary["source_status_counts"]
    cards = "".join(f'<div class="stat"><span>{label}</span><strong>{number}</strong></div>' for label, number in (
        ("歌曲資料夾", len(report.songs)), ("歌名來源檔案", len(report.files)),
        ("來源名稱待確認", summary["title_conflict_count"]), ("未取得歌名的檔案", counts["failed"])))
    auxiliary = "".join(f'<tr><td class="path">{esc(item["path"])}</td><td>{esc(item["kind"])}</td></tr>' for item in report.auxiliary_files)
    database_html = ""
    if report.database:
        stored = report.database
        totals = stored["totals"]
        database_html = f'<div class="notice"><strong>SQLite 歌曲庫已連接</strong><p class="path">{esc(stored["path"])}</p>'
        database_html += f'<p>已保存歌曲 {totals["songs"]} · 目前有來源 {totals["present_songs"]} · 未找到來源 {totals["missing_songs"]} · 已登錄遊戲難度 {totals["game_difficulties"]}</p>'
        if stored["mode"] == "stored":
            database_html += '<p>目前顯示已保存資料，沒有重新掃描；各項目可能來自不同次掃描。</p>'
        else:
            changes = stored["changes"]
            database_html += f'<p>本次新增歌曲 {changes["new_songs"]} · 內容更新 {changes["updated_songs"]} · 內容未變 {changes["unchanged_songs"]} · 重新找到 {changes["restored_songs"]}</p>'
            if not stored["complete_scan"]:
                database_html += '<p>本次掃描未完整確認範圍，因此沒有將未讀到的來源標記為缺失。</p>'
        database_html += '</div>'
    return '''<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>熱舞 Online · 歌曲清單</title><style>
:root{color-scheme:dark;--bg:#10141e;--panel:#1a2130;--line:#343e52;--text:#e9edf7;--muted:#a6b1c8;--gold:#f3cb72}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.65 "Segoe UI","Microsoft JhengHei",sans-serif}main{max-width:1600px;margin:auto;padding:32px 26px 60px}h1{margin:6px 0;font-size:30px;letter-spacing:1px}h2{font-size:21px;margin:30px 0 12px}.eyebrow{font-size:12px;letter-spacing:3px;color:var(--gold)}p{margin:10px 0}.muted,small{color:var(--muted)}small{display:block;font-size:12px}code{font:12px/1.5 Consolas,monospace;overflow-wrap:anywhere}.path{overflow-wrap:anywhere;font-size:12px}.notice{padding:15px 18px;border:1px solid #655337;border-left:4px solid var(--gold);border-radius:8px;background:#262416;margin:20px 0}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}.stat{padding:17px 21px;background:var(--panel);border:1px solid var(--line);border-radius:10px}.stat span{display:block;color:var(--muted)}.stat strong{font-size:32px;color:var(--gold)}.toolbar{display:flex;gap:10px;flex-wrap:wrap;margin:18px 0 10px}input,select{padding:11px 13px;border:1px solid #495570;border-radius:7px;background:#20293a;color:var(--text);font:inherit}input{flex:1;min-width:230px}input:focus,select:focus{outline:2px solid #a4c7ff;outline-offset:2px}.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:9px}table{border-collapse:collapse;width:100%;min-width:900px}th,td{padding:14px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{background:#252e40;color:#c7d3eb;font-size:13px;white-space:nowrap}td{background:#171e2b}td:first-child{width:29%}td:last-child{width:30%}tr:last-child td{border-bottom:0}.unknown{color:#a6b1c8;white-space:nowrap}.labels{white-space:nowrap;color:#b9cdf5;font-size:12px}.badge{display:inline-block;white-space:nowrap;font-size:12px;padding:3px 8px;border-radius:5px}.ok{background:#183b32;color:#9ee7be}.partial{background:#493c1c;color:#f6d58a}.failed{background:#4a2630;color:#ffa9b7}summary{cursor:pointer;overflow-wrap:anywhere}.source{margin-top:10px;font-size:12px}.source p{font-size:12px}.issues{padding-left:18px;font-size:12px}.issues li{margin:6px 0}.candidates{font-size:12px;color:#f6d58a}.empty{padding:20px;color:var(--muted)}footer{margin-top:30px;color:var(--muted);font-size:12px}[hidden]{display:none!important}@media(max-width:760px){main{padding:20px 14px}.stats{grid-template-columns:repeat(2,1fr)}h1{font-size:24px}.stat{padding:12px}input{min-width:100%}}
</style></head><body><main><div class="eyebrow">DANCE ONLINE / SONG TITLES / READ ONLY</div><h1>歌曲庫 · 歌名清單</h1>
''' + f'<p class="muted path">SONG：{esc(report.root)}<br>掃描：{esc(report.started_at)} → {esc(report.finished_at)}（UTC）<br>範圍：{esc("；".join(report.scope))}</p>' + '''
<div class="notice"><strong>來源檔只用來取得歌名。</strong><br>遊戲難度固定分類為 EASY / NORMAL / HARD，數值 LV 依遊戲畫面取得。自製歌有哪些難度仍待確認，不自動建立三張譜面。<br>不讀取檔案內的難度、音符或時序；這版尚未接入畫面擷取與成績紀錄。</div>
''' + database_html + ('<div class="notice">掃描已中止，本報告只有部分結果。</div>' if report.cancelled else '') + f'<div class="stats">{cards}</div><p class="muted">來源歌名已讀取 {counts["ok"]} · 待核對 {counts["partial"]} · 未取得 {counts["failed"]}</p>' + '''
<h2>歌曲</h2><div class="toolbar"><input id="search" aria-label="搜尋" placeholder="搜尋歌名、資料夾或來源檔名…"><select id="pack" aria-label="儲存群組"><option value="">全部儲存群組</option>
''' + options + '''</select><select id="status" aria-label="歌名狀態"><option value="">全部歌名狀態</option><option value="ok">歌名已讀取</option><option value="partial">歌名待核對</option><option value="failed">未取得歌名</option></select></div>
<p id="visible-count" class="muted" aria-live="polite"></p><div class="table-wrap"><table id="songs"><thead><tr><th>歌曲／資料夾</th><th>Pack</th><th>遊戲難度分類</th><th>遊戲數值</th><th>歌名來源／狀態</th></tr></thead><tbody>
''' + ''.join(rows) + '''</tbody></table><p id="no-matches" class="empty" hidden>沒有符合條件的歌曲。</p></div><h2>掃描提示</h2>
''' + (issues_html(report.diagnostics) or '<p class="muted">無</p>') + '''<h2>附屬與排除檔案</h2>''' + (
        '<details><summary>展開 ' + str(len(report.auxiliary_files)) + ' 個檔案（不作歌名來源）</summary><div class="table-wrap"><table><thead><tr><th>路徑</th><th>類型</th></tr></thead><tbody>' + auxiliary + '</tbody></table></div></details>'
        if auxiliary else '<p class="muted">無</p>') + f'<footer>v{__version__} · 每個歌曲資料夾一列，跨資料夾不自動合併。未修改原始歌曲檔案。<br>完整資料見同資料夾的 report.json；本頁尚未提供人工補認、畫面擷取或成績管理。</footer>' + '''
</main><script>
const rows=[...document.querySelectorAll('#songs tbody tr')];
const controls=['search','pack','status'].map(id=>document.getElementById(id));
function filterRows(){const [query,pack,status]=controls.map(c=>c.value.trim().toLocaleLowerCase());let count=0;for(const row of rows){const match=(!query||row.dataset.search.toLocaleLowerCase().includes(query))&&(!pack||row.dataset.pack.toLocaleLowerCase()===pack)&&(!status||row.dataset.status===status);row.hidden=!match;if(match)count++;}document.getElementById('visible-count').textContent=`顯示 ${count} / ${rows.length} 個歌曲資料夾`;document.getElementById('no-matches').hidden=count!==0;}
controls.forEach(c=>c.addEventListener('input',filterRows));filterRows();
</script></body></html>'''


def write_report(report: ScanReport, output_parent: Path) -> Path:
    output_parent = output_parent.expanduser().resolve()
    if output_parent.is_relative_to(Path(report.root).resolve()):
        raise ValueError("報告不可寫入 SONG 歌曲庫，請選擇歌曲庫以外的位置。")
    for scope in report.scope:
        if output_parent.is_relative_to(Path(scope).resolve()):
            raise ValueError("報告不可寫入掃描範圍。")
    destination = output_parent / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "report.json").write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    path = destination / "report.html"
    path.write_text(render_html(report), encoding="utf-8")
    return path
