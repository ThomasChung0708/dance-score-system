"""Real WGC/Win32 test against our own image window, NOT a real game playthrough.

The fixture changes/resizes/minimizes ONLY its own windows. No game inputs.
Run: .venv-ocr/Scripts/python.exe -X utf8 -m tools.monitor_capture_probe
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time
import tkinter as tk

from PIL import Image, ImageTk

from dance_results.window_capture import Windows, WGCCapture


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=Path('reports/phase4b/capture-probe'))
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    windows=Windows();results={'test_kind':'real_windows_capture_of_our_fixture_not_gameplay','events':[]}
    source=Image.open('tests/專案測試圖片/相同歌名2.PNG').convert('RGB').crop((0,30,1024,800))
    root=tk.Tk();root.title('dance v9.9.9 Phase4B CAPTURE TEST FIXTURE');root.geometry('1024x770+30+30')
    canvas=tk.Canvas(root,highlightthickness=0);canvas.pack(fill='both',expand=True)
    image_holder={}
    def paint(event=None):
        width=max(100,canvas.winfo_width());height=max(100,canvas.winfo_height())
        image_holder['photo']=ImageTk.PhotoImage(source.resize((width,height)))
        canvas.delete('image');canvas.create_image(0,0,anchor='nw',image=image_holder['photo'],tags='image')
    canvas.bind('<Configure>',paint);root.update();paint();root.update()
    found=[w for w in windows.list() if w['pid']==os.getpid() and 'CAPTURE TEST FIXTURE' in w['title']]
    if len(found)!=1:raise RuntimeError('Fixture window enumeration failed')
    target=found[0];capture=WGCCapture(target['hwnd'],2)
    started=time.monotonic();samples=[];overlay=None;minimized=False;resumed=False;resized=False;tick_count=0
    def tick():
        nonlocal overlay,minimized,resumed,resized,tick_count
        elapsed=time.monotonic()-started;tick_count+=1
        # Invalidate only our own fixture so WGC has fresh samples even for a static image.
        canvas.delete('pulse');canvas.create_rectangle(0,0,5,5,fill='white' if tick_count%2 else 'black',outline='',tags='pulse')
        item=capture.take()
        if item:
            _,image=item;info=windows.info(target['hwnd'])
            row=dict(elapsed=round(elapsed,2),size=list(image.size),foreground=info['foreground'],copy_ms=capture.copy_ms)
            samples.append(row)
            if len(samples)==1 or (overlay is not None and not info['foreground'] and not (args.output/'occluded.png').exists()):
                image.save(args.output/('first.png' if len(samples)==1 else 'occluded.png'))
        if elapsed>2 and overlay is None:
            overlay=tk.Toplevel(root);overlay.title('Phase4B test occluder');overlay.geometry('1040x815+25+25')
            tk.Label(overlay,text='WGC background capture verification\nThis is a test fixture, not gameplay.',font=('Arial',22)).pack(expand=True)
            overlay.lift();overlay.focus_force()
            results['events'].append(dict(event='own_fixture_occluded',at=elapsed))
        if elapsed>4 and not resized:
            root.geometry('850x650+30+30');resized=True
            results['events'].append(dict(event='own_fixture_resized',at=elapsed))
        if elapsed>6 and not minimized:
            root.iconify();minimized=True;root.update_idletasks()
            results['minimized_detected']=windows.info(target['hwnd'])['minimized']
        if elapsed>7 and minimized and not resumed:
            root.deiconify();resumed=True
        if elapsed>9:
            capture.stop();old=target['hwnd'];root.destroy()
            results['closed_detected']=windows.info(old) is None
            results['initial_window']=target;results['samples']=samples
            results['occluded_capture']=any(not s['foreground'] for s in samples)
            results['resize_capture']=len({tuple(s['size']) for s in samples})>1
            return
        root.after(150,tick)
    root.after(150,tick);root.mainloop()
    # A new native window after closure exercises re-discovery, independent of version suffix.
    second=tk.Tk();second.title('dance v10.0.0 Phase4B CAPTURE TEST FIXTURE RESTART');second.geometry('400x300+60+60')
    tk.Label(second,text='Restart capture verification',font=('Arial',16)).pack(expand=True);second.update()
    found=[w for w in windows.list() if w['pid']==os.getpid() and 'FIXTURE RESTART' in w['title']]
    results['restart_discovered']=len(found)==1
    if found:
        cap=WGCCapture(found[0]['hwnd'],2);deadline=time.monotonic()+4;frame=None
        while time.monotonic()<deadline and frame is None:
            second.update();frame=cap.take();time.sleep(.05)
        results['restart_capture']=frame is not None
        cap.stop()
    second.destroy()
    results['checks_passed']=all(results.get(k) for k in
        ['occluded_capture','resize_capture','minimized_detected','closed_detected','restart_discovered','restart_capture'])
    (args.output/'probe.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in results.items() if k!='samples'},ensure_ascii=False,indent=2))
    return 0 if results['checks_passed'] else 2


if __name__=='__main__':raise SystemExit(main())
