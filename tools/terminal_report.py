"""Write a self-contained replay report from captured terminal grid frames."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List


def _json_for_script(value: Any) -> str:
    """Serialize JSON so hostile text cannot terminate the data script."""
    encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    return (
        encoded.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _screen_text(frame: Dict[str, Any]) -> str:
    lines = [str(line) for line in frame.get("lines", [])]
    rows = int(frame.get("rows", len(lines)))
    cols = int(frame.get("cols", max([len(line) for line in lines] or [0])))
    return "\n".join(
        [
            "=== {step} @ {time:.3f}s ({rows}x{cols}) ===".format(
                step=frame.get("step", ""),
                time=float(frame.get("time", 0.0)),
                rows=rows,
                cols=cols,
            )
        ]
        + lines
    )


def write_report(output_dir: Path, report: dict, frames: list[dict]) -> None:
    """Write JSON artifacts, final-step screens, and an offline HTML replay."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_data = dict(report)
    frame_data = [dict(frame) for frame in frames]

    (output_dir / "report.json").write_text(
        json.dumps(report_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "frames.json").write_text(
        json.dumps(frame_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    screens = "\n\n".join(_screen_text(frame) for frame in frame_data)
    if screens:
        screens += "\n"
    (output_dir / "screens.txt").write_text(screens, encoding="utf-8")

    payload = _json_for_script({"report": report_data, "frames": frame_data})
    # Raw template keeps JavaScript escape sequences (for example ``\n`` in
    # ``join('\\n')``) intact instead of turning them into source newlines.
    html = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Terminal replay report</title>
<style>
body{font:14px system-ui,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem;color:#202124}
.notice{padding:.8rem 1rem;background:#fff3cd;border:1px solid #e0b84c;font-weight:700}
.controls{display:flex;gap:.5rem;align-items:center;flex-wrap:wrap;margin:1rem 0}
button,select{font:inherit;padding:.35rem .6rem} #screen{background:#111;color:#eee;padding:1rem;overflow:auto;white-space:pre;font:14px/1.35 monospace;min-height:8rem}
dt{font-weight:700}dd{margin:0 0 .4rem 0}.ok{color:#176b2c}.bad{color:#a11}
</style></head><body>
<div class="notice">从真实终端输出重建，不是原生窗口截图；字体/DPI/IME仍需人工验收。</div>
<h1>Terminal replay report</h1><dl id="summary"></dl>
<div class="controls"><button id="prev" type="button">上一帧</button><button id="next" type="button">下一帧</button><button id="play" type="button">播放</button><button id="pause" type="button">暂停</button><label>时间 <select id="time"></select></label><span id="position"></span></div>
<h2 id="step"></h2><pre id="screen" aria-live="polite"></pre>
<script id="report-data" type="application/json">%s</script>
<script>
const data=JSON.parse(document.getElementById('report-data').textContent);
const frames=data.frames||[], report=data.report||{}; let index=0, timer=null;
const screen=document.getElementById('screen'), step=document.getElementById('step'), pos=document.getElementById('position'), time=document.getElementById('time');
const text=(id,value)=>{document.getElementById(id).textContent=value;};
function render(){if(!frames.length){text('step','没有捕获帧');screen.textContent='本次报告没有可回放的真实终端帧。';pos.textContent='0 / 0';return;} const f=frames[index]; text('step',(f.step||'')+' — '+(f.rows||0)+'×'+(f.cols||0)); screen.textContent=(f.lines||[]).join('\n'); pos.textContent=(index+1)+' / '+frames.length; time.value=String(index);}
function fill(){frames.forEach((f,i)=>{const o=document.createElement('option');o.value=String(i);o.textContent=(f.time??0)+'s — '+(f.step||'');time.appendChild(o);});}
function move(delta){if(frames.length){index=Math.max(0,Math.min(frames.length-1,index+delta));render();}}
document.getElementById('prev').onclick=()=>move(-1); document.getElementById('next').onclick=()=>move(1); time.onchange=()=>{index=Number(time.value);render();};
document.getElementById('play').onclick=()=>{if(timer||frames.length<2)return; timer=setInterval(()=>{if(index>=frames.length-1){clearInterval(timer);timer=null;}else move(1);},700);};
document.getElementById('pause').onclick=()=>{if(timer){clearInterval(timer);timer=null;}};
const summary=document.getElementById('summary'); [['status',report.passed?'PASS':'FAIL'],['platform',report.platform||'' ],['backend',report.backend||'' ],['python',report.python||'' ],['failure',report.failure||'None']].forEach(([k,v])=>{const d=document.createElement('div');d.textContent=k+': '+v;summary.appendChild(d);});
fill();render();
</script></body></html>
""" % payload
    (output_dir / "replay.html").write_text(html, encoding="utf-8")
