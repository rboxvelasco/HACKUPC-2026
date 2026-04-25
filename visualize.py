#!/usr/bin/env python3
"""
Generate an HTML visualization of a warehouse solution.

Usage:
    Single-solution:
        python3 visualize.py <case_dir> <solution_file> [output_html]

    Compare two solutions side-by-side (e.g. greedy vs SA):
        python3 visualize.py <case_dir> <solution_a> --compare <solution_b> \
                             [--labels A,B] [-o output_html]

In compare mode, the HTML includes a toggle to flip between the two
placements over the same warehouse, plus a Δ row showing the metric diff.
"""

import argparse
import json
import os
import sys
from typing import List, Tuple

from solver import (
    PlacedBay,
    compute_score,
    parse_bay_types,
    parse_ceiling,
    parse_obstacles,
    parse_warehouse,
    usable_area,
)


# ─────────────────────────────────────────────
# Load a single solution file into JS-ready dicts
# ─────────────────────────────────────────────

def _load_placed(solution_file: str, bay_type_map: dict) -> List[PlacedBay]:
    placed: List[PlacedBay] = []
    with open(solution_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(',')
            bay_id = int(parts[0].strip())
            x = int(parts[1].strip())
            y = int(parts[2].strip())
            rot = int(parts[3].strip())
            placed.append(PlacedBay(
                bay_type=bay_type_map[bay_id], x=x, y=y, rotation=rot,
            ))
    return placed


def _solution_payload(placed: List[PlacedBay], usable_area_val: float) -> dict:
    """Turn a placement into the JSON blob the HTML consumes.

    `usable_area_val` is the obstacle-corrected area (see solver.usable_area).
    """
    bays_data = []
    for i, pb in enumerate(placed):
        w, d = pb.get_body_dims()
        bg = pb.get_body_with_gap_polygon()
        gminx, gminy, gmaxx, gmaxy = bg.bounds
        bays_data.append({
            'i': i, 'tid': pb.bay_type.id,
            'x': pb.x, 'y': pb.y, 'w': w, 'd': d, 'rot': pb.rotation,
            'h': pb.bay_type.height, 'gap': pb.bay_type.gap,
            'pr': pb.bay_type.price, 'ld': pb.bay_type.n_loads,
            'gx': gminx, 'gy': gminy, 'gw': gmaxx - gminx, 'gh': gmaxy - gminy,
        })

    total_price = sum(pb.bay_type.price for pb in placed)
    total_loads = sum(pb.bay_type.n_loads for pb in placed)
    total_area = sum(pb.bay_type.area for pb in placed)
    return {
        'bays': bays_data,
        'stats': {
            'n_bays': len(placed),
            'coverage': total_area / usable_area_val if usable_area_val else 0,
            'score': compute_score(placed, usable_area_val) if placed else 0,
            'price_per_load': total_price / total_loads if total_loads else 0,
            'total_price': total_price,
            'total_loads': total_loads,
        }
    }


# ─────────────────────────────────────────────
# HTML emission
# ─────────────────────────────────────────────

def generate_html(
    case_dir: str,
    solution_files: List[str],
    labels: List[str],
    output_html: str,
) -> None:
    """Emit a single HTML that shows one or two solutions.

    If `solution_files` has length 1, the sidebar only shows that solution.
    If length 2, the sidebar shows a toggle + a Δ row comparing labels[0] → labels[1].
    """
    assert 1 <= len(solution_files) <= 2
    assert len(labels) == len(solution_files)

    warehouse = parse_warehouse(os.path.join(case_dir, 'warehouse.csv'))
    obstacles = parse_obstacles(os.path.join(case_dir, 'obstacles.csv'))
    bay_types = parse_bay_types(os.path.join(case_dir, 'types_of_bays.csv'))
    bay_type_map = {bt.id: bt for bt in bay_types}

    usable_area_val = usable_area(warehouse, obstacles)
    solutions_payload = []
    for sf, lbl in zip(solution_files, labels):
        placed = _load_placed(sf, bay_type_map)
        payload = _solution_payload(placed, usable_area_val)
        payload['label'] = lbl
        solutions_payload.append(payload)

    # Warehouse + obstacles payload (shared across both views)
    wx, wy = warehouse.exterior.xy
    min_x, max_x = min(wx), max(wx)
    min_y, max_y = min(wy), max(wy)
    wh_coords = list(zip([int(x) for x in wx], [int(y) for y in wy]))
    obs_data = []
    for obs in obstacles:
        bminx, bminy, bmaxx, bmaxy = obs.bounds
        obs_data.append({
            'x': bminx, 'y': bminy,
            'w': bmaxx - bminx, 'h': bmaxy - bminy,
        })

    case_name = os.path.basename(case_dir)
    compare_mode = len(solutions_payload) == 2

    html = _build_html(
        case_name=case_name,
        wh_coords=wh_coords,
        obs_data=obs_data,
        solutions=solutions_payload,
        bbox=(min_x, min_y, max_x, max_y),
        compare_mode=compare_mode,
    )

    with open(output_html, 'w') as f:
        f.write(html)
    print(f"Visualization written to {output_html}")


def _build_html(
    case_name: str,
    wh_coords: List[Tuple[int, int]],
    obs_data: list,
    solutions: list,
    bbox: Tuple[float, float, float, float],
    compare_mode: bool,
) -> str:
    min_x, min_y, max_x, max_y = bbox

    # Stats panel — either single view or compare view.
    # We emit an HTML container that JS will populate dynamically so the stats
    # update when the user toggles between solutions.
    subtitle = "Greedy vs SA" if compare_mode else "Warehouse Optimizer"

    # Compare-mode switcher in the controls block.
    switcher_html = ""
    if compare_mode:
        lbl_a = solutions[0]['label']
        lbl_b = solutions[1]['label']
        switcher_html = f"""
    <div class="controls">
        <h3>Solution</h3>
        <div class="switch">
            <button class="switch-btn active" data-sol="0">{lbl_a}</button>
            <button class="switch-btn" data-sol="1">{lbl_b}</button>
        </div>
    </div>
"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{case_name} — Warehouse Optimizer</title>
<style>
:root {{
    --bg: #f5f5f7;
    --card: #ffffff;
    --text: #1d1d1f;
    --text2: #86868b;
    --border: #d2d2d7;
    --accent: #0071e3;
    --red: #ff3b30;
    --green: #34c759;
    --orange: #ff9500;
}}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{
    font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display',
                 'Helvetica Neue', Arial, sans-serif;
    background: var(--bg); color: var(--text);
    -webkit-font-smoothing: antialiased;
    overflow: hidden; height: 100vh;
}}

/* Sidebar */
.sidebar {{
    position: fixed; left: 0; top: 0; bottom: 0; width: 280px;
    background: var(--card); border-right: 1px solid var(--border);
    display: flex; flex-direction: column; z-index: 10;
}}
.sidebar-header {{
    padding: 24px 20px 16px;
    border-bottom: 1px solid var(--border);
}}
.sidebar-header h1 {{
    font-size: 20px; font-weight: 600; letter-spacing: -0.3px;
}}
.sidebar-header .subtitle {{
    font-size: 13px; color: var(--text2); margin-top: 2px;
}}

/* Stats grid */
.stats {{
    display: grid; grid-template-columns: 1fr 1fr;
    gap: 1px; background: var(--border);
    border-bottom: 1px solid var(--border);
}}
.stat {{ background: var(--card); padding: 14px 16px; }}
.stat .value {{
    font-size: 24px; font-weight: 600; letter-spacing: -0.5px;
    font-variant-numeric: tabular-nums;
}}
.stat .value.green {{ color: var(--green); }}
.stat .value.orange {{ color: var(--orange); }}
.stat .value.red {{ color: var(--red); }}
.stat .label {{
    font-size: 11px; color: var(--text2); text-transform: uppercase;
    letter-spacing: 0.5px; margin-top: 2px;
}}
.stat .delta {{
    font-size: 11px; margin-top: 4px;
    font-variant-numeric: tabular-nums;
}}
.delta.up {{ color: var(--green); }}
.delta.down {{ color: var(--red); }}
.delta.flat {{ color: var(--text2); }}

/* Controls */
.controls {{
    padding: 16px 20px; display: flex; flex-direction: column; gap: 10px;
    border-bottom: 1px solid var(--border);
}}
.controls h3 {{
    font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px;
    color: var(--text2); margin-bottom: 2px;
}}
.toggle {{
    display: flex; align-items: center; justify-content: space-between;
    font-size: 14px;
}}
.toggle input[type=checkbox] {{
    appearance: none; -webkit-appearance: none;
    width: 40px; height: 24px; border-radius: 12px;
    background: #d2d2d7; position: relative; cursor: pointer;
    transition: background 0.2s;
}}
.toggle input[type=checkbox]:checked {{ background: var(--accent); }}
.toggle input[type=checkbox]::after {{
    content: ''; position: absolute; top: 2px; left: 2px;
    width: 20px; height: 20px; border-radius: 10px;
    background: white; transition: transform 0.2s;
    box-shadow: 0 1px 3px rgba(0,0,0,0.2);
}}
.toggle input[type=checkbox]:checked::after {{ transform: translateX(16px); }}

/* Solution switcher */
.switch {{
    display: flex; gap: 0;
    border: 1px solid var(--border); border-radius: 10px; overflow: hidden;
}}
.switch-btn {{
    flex: 1; background: var(--card); border: none; cursor: pointer;
    padding: 10px 14px; font-size: 13px; color: var(--text);
    transition: background 0.15s;
    font-weight: 500;
    border-right: 1px solid var(--border);
}}
.switch-btn:last-child {{ border-right: none; }}
.switch-btn:hover {{ background: var(--bg); }}
.switch-btn.active {{
    background: var(--accent); color: white;
}}

/* Bay list */
.bay-list-header {{
    padding: 16px 20px 8px;
    font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px;
    color: var(--text2);
}}
.bay-list {{
    flex: 1; overflow-y: auto; padding: 0 12px 12px;
}}
.bay-item {{
    display: flex; align-items: center; gap: 10px;
    padding: 8px; border-radius: 8px; cursor: pointer;
    transition: background 0.15s; font-size: 13px;
}}
.bay-item:hover {{ background: var(--bg); }}
.bay-item.active {{ background: #0071e315; }}
.bay-dot {{
    width: 10px; height: 10px; border-radius: 3px; flex-shrink: 0;
}}
.bay-item .info {{ flex: 1; }}
.bay-item .info .name {{ font-weight: 500; }}
.bay-item .info .detail {{ font-size: 11px; color: var(--text2); }}

/* Canvas area */
.canvas-wrap {{
    margin-left: 280px; height: 100vh; position: relative;
    background: var(--bg);
}}
canvas {{
    display: block; width: 100%; height: 100%;
    cursor: grab;
}}
canvas:active {{ cursor: grabbing; }}

.tooltip {{
    position: fixed; background: var(--card);
    border: 1px solid var(--border);
    padding: 10px 14px; border-radius: 10px;
    font-size: 12px; pointer-events: none;
    display: none; z-index: 100;
    box-shadow: 0 4px 20px rgba(0,0,0,0.08);
    line-height: 1.5;
    backdrop-filter: blur(20px);
    -webkit-backdrop-filter: blur(20px);
}}
.tooltip b {{ font-weight: 600; }}
.tooltip .dim {{ color: var(--text2); }}

.zoom-controls {{
    position: absolute; bottom: 20px; right: 20px;
    display: flex; flex-direction: column; gap: 1px;
    background: var(--border); border-radius: 10px;
    overflow: hidden;
    box-shadow: 0 2px 10px rgba(0,0,0,0.06);
}}
.zoom-btn {{
    width: 40px; height: 36px; background: var(--card);
    border: none; font-size: 18px; cursor: pointer;
    color: var(--text); display: flex; align-items: center;
    justify-content: center; transition: background 0.15s;
}}
.zoom-btn:hover {{ background: var(--bg); }}
.zoom-btn:active {{ background: #e8e8ed; }}
</style>
</head>
<body>

<div class="sidebar">
    <div class="sidebar-header">
        <h1>{case_name}</h1>
        <div class="subtitle">{subtitle}</div>
    </div>

    <div class="stats" id="statsGrid">
        <!-- populated by JS -->
    </div>

{switcher_html}

    <div class="controls">
        <h3>Display</h3>
        <div class="toggle"><span>Gaps</span><input type="checkbox" id="showGaps" checked></div>
        <div class="toggle"><span>Obstacles</span><input type="checkbox" id="showObstacles" checked></div>
        <div class="toggle"><span>Labels</span><input type="checkbox" id="showLabels" checked></div>
        <div class="toggle"><span>Grid</span><input type="checkbox" id="showGrid"></div>
    </div>

    <div class="bay-list-header" id="bayListHeader">Placed Bays</div>
    <div class="bay-list" id="bayList"></div>
</div>

<div class="canvas-wrap">
    <canvas id="canvas"></canvas>
    <div class="zoom-controls">
        <button class="zoom-btn" id="zoomIn">+</button>
        <button class="zoom-btn" id="zoomOut">−</button>
        <button class="zoom-btn" id="zoomFit" style="font-size:13px">⌂</button>
    </div>
</div>

<div class="tooltip" id="tooltip"></div>

<script>
const WH = {json.dumps(wh_coords)};
const OBS = {json.dumps(obs_data)};
const SOLUTIONS = {json.dumps(solutions)};
const COMPARE = {json.dumps(compare_mode)};
const B = {{ x0:{min_x}, y0:{min_y}, x1:{max_x}, y1:{max_y} }};

const canvas = document.getElementById('canvas');
const ctx = canvas.getContext('2d');
const tip = document.getElementById('tooltip');

// Soft palette
const C = [
    '#5e5ce6','#30b0c7','#34c759','#ff9500','#ff3b30','#af52de',
    '#007aff','#ff2d55','#5ac8fa','#ffcc00','#64d2ff','#bf5af2',
    '#ac8e68','#8e8e93','#a2845e','#0a84ff'
];

let activeIdx = 0;
let BAYS = SOLUTIONS[activeIdx].bays;

let sc = 1, ox = 0, oy = 0;
let drag = false, dx0, dy0;
let hov = -1, sel = -1;

function resize() {{
    const r = canvas.parentElement.getBoundingClientRect();
    canvas.width = r.width * devicePixelRatio;
    canvas.height = r.height * devicePixelRatio;
    canvas.style.width = r.width + 'px';
    canvas.style.height = r.height + 'px';
    ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
    fit(); draw();
}}

function fit() {{
    const cw = canvas.width / devicePixelRatio;
    const ch = canvas.height / devicePixelRatio;
    const pad = 40;
    const sx = (cw - pad*2) / (B.x1 - B.x0);
    const sy = (ch - pad*2) / (B.y1 - B.y0);
    sc = Math.min(sx, sy);
    ox = (cw - (B.x1-B.x0)*sc)/2 - B.x0*sc;
    oy = (ch - (B.y1-B.y0)*sc)/2 - B.y0*sc;
}}

function ts(x, y) {{
    const ch = canvas.height / devicePixelRatio;
    return [x*sc+ox, ch-(y*sc+oy)];
}}
function tw(sx, sy) {{
    const ch = canvas.height / devicePixelRatio;
    return [(sx-ox)/sc, (ch-sy-oy)/sc];
}}

function roundRect(x, y, w, h, r) {{
    r = Math.min(r, w/2, h/2);
    ctx.beginPath();
    ctx.moveTo(x+r, y);
    ctx.lineTo(x+w-r, y); ctx.arcTo(x+w, y, x+w, y+r, r);
    ctx.lineTo(x+w, y+h-r); ctx.arcTo(x+w, y+h, x+w-r, y+h, r);
    ctx.lineTo(x+r, y+h); ctx.arcTo(x, y+h, x, y+h-r, r);
    ctx.lineTo(x, y+r); ctx.arcTo(x, y, x+r, y, r);
    ctx.closePath();
}}

function draw() {{
    const cw = canvas.width / devicePixelRatio;
    const ch = canvas.height / devicePixelRatio;
    ctx.clearRect(0, 0, cw, ch);

    const sGap = document.getElementById('showGaps').checked;
    const sObs = document.getElementById('showObstacles').checked;
    const sLbl = document.getElementById('showLabels').checked;
    const sGrid = document.getElementById('showGrid').checked;

    if (sGrid) {{
        const span = Math.max(B.x1-B.x0, B.y1-B.y0);
        const step = Math.pow(10, Math.floor(Math.log10(span))-1);
        ctx.strokeStyle = '#00000008';
        ctx.lineWidth = 1;
        for (let gx = Math.floor(B.x0/step)*step; gx <= B.x1; gx += step) {{
            const [sx] = ts(gx, 0);
            ctx.beginPath(); ctx.moveTo(sx, 0); ctx.lineTo(sx, ch); ctx.stroke();
        }}
        for (let gy = Math.floor(B.y0/step)*step; gy <= B.y1; gy += step) {{
            const [,sy] = ts(0, gy);
            ctx.beginPath(); ctx.moveTo(0, sy); ctx.lineTo(cw, sy); ctx.stroke();
        }}
    }}

    // Warehouse fill
    ctx.beginPath();
    for (let i = 0; i < WH.length; i++) {{
        const [sx, sy] = ts(WH[i][0], WH[i][1]);
        i === 0 ? ctx.moveTo(sx, sy) : ctx.lineTo(sx, sy);
    }}
    ctx.closePath();
    ctx.fillStyle = '#ffffff';
    ctx.fill();
    ctx.strokeStyle = '#d2d2d7';
    ctx.lineWidth = 1.5;
    ctx.stroke();

    // Obstacles
    if (sObs) {{
        for (const o of OBS) {{
            const [sx, sy] = ts(o.x, o.y + o.h);
            const w = o.w*sc, h = o.h*sc;
            const r = Math.min(3, w/4, h/4);
            roundRect(sx, sy, w, h, r);
            ctx.fillStyle = '#ff3b3012';
            ctx.fill();
            ctx.strokeStyle = '#ff3b3040';
            ctx.lineWidth = 1;
            ctx.stroke();
        }}
    }}

    // Gaps
    if (sGap) {{
        for (const b of BAYS) {{
            const [gsx, gsy] = ts(b.gx, b.gy + b.gh);
            const gw = b.gw*sc, gh = b.gh*sc;
            ctx.fillStyle = '#0071e306';
            ctx.fillRect(gsx, gsy, gw, gh);
            ctx.strokeStyle = '#0071e318';
            ctx.lineWidth = 0.5;
            ctx.setLineDash([3, 3]);
            ctx.strokeRect(gsx, gsy, gw, gh);
            ctx.setLineDash([]);
        }}
    }}

    // Bays
    for (let i = 0; i < BAYS.length; i++) {{
        const b = BAYS[i];
        const [sx, sy] = ts(b.x, b.y + b.d);
        const w = b.w*sc, h = b.d*sc;
        const c = C[b.tid % C.length];
        const isH = i === hov;
        const isS = i === sel;
        const r = Math.min(2, w/4, h/4);

        if (isH || isS) {{
            ctx.save();
            ctx.shadowColor = c + '40';
            ctx.shadowBlur = 12;
            ctx.shadowOffsetY = 2;
        }}

        roundRect(sx, sy, w, h, r);
        ctx.fillStyle = isH || isS ? c + 'dd' : c + '99';
        ctx.fill();
        ctx.strokeStyle = isH || isS ? c : c + 'cc';
        ctx.lineWidth = isH || isS ? 1.5 : 0.5;
        ctx.stroke();

        if (isH || isS) ctx.restore();

        if (sLbl && w > 18 && h > 12) {{
            ctx.fillStyle = '#fff';
            const fs = Math.min(11, Math.max(7, Math.min(w, h) * 0.45));
            ctx.font = `600 ${{fs}}px -apple-system, sans-serif`;
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(b.tid, sx + w/2, sy + h/2);
        }}
    }}

    // Warehouse outline (on top)
    ctx.beginPath();
    for (let i = 0; i < WH.length; i++) {{
        const [sx, sy] = ts(WH[i][0], WH[i][1]);
        i === 0 ? ctx.moveTo(sx, sy) : ctx.lineTo(sx, sy);
    }}
    ctx.closePath();
    ctx.strokeStyle = '#1d1d1f';
    ctx.lineWidth = 1.5;
    ctx.stroke();
}}

// ─── stats rendering ───────────────────────────────────────
function fmtCoverage(c) {{ return (c * 100).toFixed(0) + '%'; }}
function fmtScore(s)    {{ return s.toFixed(0); }}
function fmtPPL(p)      {{ return p.toFixed(0); }}

function deltaHtml(cur, other, lowerIsBetter, fmt) {{
    if (other === null || other === undefined) return '';
    const d = cur - other;
    if (Math.abs(d) < 0.5 && fmt === fmtScore) return `<div class="delta flat">±0 vs ${{fmt(other)}}</div>`;
    let cls = 'flat';
    if (d > 0) cls = lowerIsBetter ? 'down' : 'up';
    if (d < 0) cls = lowerIsBetter ? 'up' : 'down';
    const sign = d > 0 ? '+' : '';
    // Coverage delta: show percentage-point diff
    if (fmt === fmtCoverage) {{
        const dd = (d * 100);
        const s = dd > 0 ? '+' : '';
        return `<div class="delta ${{cls}}">${{s}}${{dd.toFixed(1)}}pp vs ${{fmt(other)}}</div>`;
    }}
    return `<div class="delta ${{cls}}">${{sign}}${{fmt(d)}} vs ${{fmt(other)}}</div>`;
}}

function renderStats() {{
    const s = SOLUTIONS[activeIdx].stats;
    const other = COMPARE ? SOLUTIONS[1 - activeIdx].stats : null;

    const covClass = s.coverage > 0.6 ? 'green' : s.coverage > 0.4 ? 'orange' : 'red';

    document.getElementById('statsGrid').innerHTML = `
        <div class="stat">
            <div class="value">${{s.n_bays}}</div>
            <div class="label">Bays</div>
            ${{other ? deltaHtml(s.n_bays, other.n_bays, false, v => v.toFixed(0)) : ''}}
        </div>
        <div class="stat">
            <div class="value ${{covClass}}">${{fmtCoverage(s.coverage)}}</div>
            <div class="label">Coverage</div>
            ${{other ? deltaHtml(s.coverage, other.coverage, false, fmtCoverage) : ''}}
        </div>
        <div class="stat">
            <div class="value">${{fmtScore(s.score)}}</div>
            <div class="label">Score Q</div>
            ${{other ? deltaHtml(s.score, other.score, true, fmtScore) : ''}}
        </div>
        <div class="stat">
            <div class="value">${{fmtPPL(s.price_per_load)}}</div>
            <div class="label">Price / Load</div>
            ${{other ? deltaHtml(s.price_per_load, other.price_per_load, true, fmtPPL) : ''}}
        </div>
    `;
}}

function renderBayList() {{
    const list = document.getElementById('bayList');
    list.innerHTML = '';
    document.getElementById('bayListHeader').textContent =
        `Placed Bays (${{BAYS.length}})`;
    BAYS.forEach((b, i) => {{
        const el = document.createElement('div');
        el.className = 'bay-item';
        el.dataset.idx = i;
        el.innerHTML = `
            <div class="bay-dot" style="background:${{C[b.tid % C.length]}}"></div>
            <div class="info">
                <div class="name">Type ${{b.tid}}</div>
                <div class="detail">${{b.w}}×${{b.d}} at (${{b.x}},${{b.y}}) ${{b.rot}}°</div>
            </div>
        `;
        el.addEventListener('mouseenter', () => {{ hov = i; draw(); }});
        el.addEventListener('mouseleave', () => {{ hov = -1; draw(); }});
        el.addEventListener('click', () => {{
            sel = sel === i ? -1 : i;
            document.querySelectorAll('.bay-item').forEach(e => e.classList.remove('active'));
            if (sel >= 0) el.classList.add('active');
            draw();
        }});
        list.appendChild(el);
    }});
}}

function switchSolution(idx) {{
    activeIdx = idx;
    BAYS = SOLUTIONS[activeIdx].bays;
    hov = -1;
    sel = -1;
    renderStats();
    renderBayList();
    draw();
}}

if (COMPARE) {{
    document.querySelectorAll('.switch-btn').forEach(btn => {{
        btn.addEventListener('click', () => {{
            document.querySelectorAll('.switch-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            switchSolution(parseInt(btn.dataset.sol));
        }});
    }});
    // Keyboard toggle: 1 / 2 or Tab.
    document.addEventListener('keydown', (e) => {{
        if (e.key === '1') switchSolution(0);
        else if (e.key === '2') switchSolution(1);
        else if (e.key === 'Tab') {{
            e.preventDefault();
            const next = 1 - activeIdx;
            switchSolution(next);
            document.querySelectorAll('.switch-btn').forEach((b, i) =>
                b.classList.toggle('active', i === next));
        }}
    }});
}}

// Canvas interactions
canvas.addEventListener('wheel', (e) => {{
    e.preventDefault();
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const z = e.deltaY < 0 ? 1.12 : 1/1.12;
    const [wx, wy] = tw(mx, my);
    sc *= z;
    const ch = canvas.height / devicePixelRatio;
    ox = mx - wx*sc;
    oy = -(my - ch) - wy*sc;
    draw();
}});

canvas.addEventListener('mousedown', (e) => {{ drag = true; dx0 = e.clientX; dy0 = e.clientY; }});
canvas.addEventListener('mousemove', (e) => {{
    if (drag) {{
        ox += e.clientX - dx0; oy -= e.clientY - dy0;
        dx0 = e.clientX; dy0 = e.clientY;
        draw(); return;
    }}
    const rect = canvas.getBoundingClientRect();
    const [wx, wy] = tw(e.clientX - rect.left, e.clientY - rect.top);
    let f = -1;
    for (let i = BAYS.length-1; i >= 0; i--) {{
        const b = BAYS[i];
        if (wx >= b.x && wx <= b.x+b.w && wy >= b.y && wy <= b.y+b.d) {{ f = i; break; }}
    }}
    if (f !== hov) {{ hov = f; draw(); }}
    if (f >= 0) {{
        const b = BAYS[f];
        tip.style.display = 'block';
        tip.style.left = (e.clientX + 14) + 'px';
        tip.style.top = (e.clientY + 14) + 'px';
        tip.innerHTML = `<b>Type ${{b.tid}}</b> <span class="dim">#${{f}}</span><br>`
            + `${{b.w}} × ${{b.d}} × ${{b.h}} mm<br>`
            + `<span class="dim">Position</span> (${{b.x}}, ${{b.y}}) ${{b.rot}}°<br>`
            + `<span class="dim">Gap</span> ${{b.gap}} mm<br>`
            + `<span class="dim">Price</span> ${{b.pr}} &nbsp; <span class="dim">Loads</span> ${{b.ld}}`;
    }} else tip.style.display = 'none';
}});
canvas.addEventListener('mouseup', () => drag = false);
canvas.addEventListener('mouseleave', () => {{ drag = false; tip.style.display = 'none'; }});

document.getElementById('zoomIn').addEventListener('click', () => {{
    const ch = canvas.height / devicePixelRatio;
    const cw = canvas.width / devicePixelRatio;
    const [wx, wy] = tw(cw/2, ch/2);
    sc *= 1.3;
    ox = cw/2 - wx*sc;
    oy = -(ch/2 - ch) - wy*sc;
    draw();
}});
document.getElementById('zoomOut').addEventListener('click', () => {{
    const ch = canvas.height / devicePixelRatio;
    const cw = canvas.width / devicePixelRatio;
    const [wx, wy] = tw(cw/2, ch/2);
    sc /= 1.3;
    ox = cw/2 - wx*sc;
    oy = -(ch/2 - ch) - wy*sc;
    draw();
}});
document.getElementById('zoomFit').addEventListener('click', () => {{ fit(); draw(); }});

['showGaps','showObstacles','showLabels','showGrid'].forEach(id =>
    document.getElementById(id).addEventListener('change', draw));

window.addEventListener('resize', resize);

// Initial render
renderStats();
renderBayList();
resize();
</script>
</body>
</html>"""

    return html


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

def _parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Render an HTML visualization of a warehouse solution, "
                    "optionally comparing two solutions.",
    )
    p.add_argument('case_dir', help="Case directory (contains *.csv inputs).")
    p.add_argument('solution_a', help="Solution CSV to visualize.")
    p.add_argument('--compare', dest='solution_b', default=None,
                   help="Second solution CSV; enables compare mode.")
    p.add_argument('--labels', default='Greedy,SA',
                   help="Comma-separated labels for the two solutions "
                        "(compare mode only). Default: 'Greedy,SA'.")
    p.add_argument('-o', '--output', default=None,
                   help="Output HTML path.")
    # Backwards-compat: allow `visualize.py case sol out.html` as positional
    # when --compare is not used.
    p.add_argument('legacy_output', nargs='?', default=None,
                   help=argparse.SUPPRESS)
    return p.parse_args(argv)


if __name__ == '__main__':
    args = _parse_args(sys.argv[1:])

    solution_files = [args.solution_a]
    labels = ['Solution']
    if args.solution_b:
        solution_files.append(args.solution_b)
        parts = [s.strip() for s in args.labels.split(',')]
        if len(parts) != 2:
            print("ERROR: --labels must contain exactly two comma-separated values", file=sys.stderr)
            sys.exit(2)
        labels = parts

    output_html = args.output or args.legacy_output
    if output_html is None:
        output_html = 'visualization.html' if not args.solution_b else 'compare.html'

    generate_html(args.case_dir, solution_files, labels, output_html)
