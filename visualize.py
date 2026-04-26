#!/usr/bin/env python3
"""
Generate an HTML visualization of a warehouse solution.

Features
--------
- Dual 2D / 3D views (toggle in the header)
- Smooth canvas pan/zoom (2D) and orbit controls (3D, via three.js)
- Obstacles rendered as columns extruded to the local ceiling in 3D
- Variable ceiling rendered as a translucent mesh in 3D
- Bays rendered with their true `height` in 3D, and gap aisles as
  translucent slabs on the floor
- Sidebar with stats, display toggles, and a bay list (click to focus
  the camera / highlight)
- Compare mode: pass two solution files and toggle between them

Usage
-----
Single solution:
    python3 visualize.py <case_dir> <solution_file> [output_html]

Compare two solutions:
    python3 visualize.py <case_dir> <solution_a> --compare <solution_b> \\
                         [--labels A,B] [-o output_html]
"""

import argparse
import json
import os
import sys
from typing import List, Tuple

from solver import (
    Ceiling,
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
    """Turn a placement into the JSON blob the HTML consumes."""
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


def _ceiling_payload(ceiling: Ceiling, x_min: float, x_max: float) -> dict:
    """Serialize ceiling as a list of (x_start, x_end, height) segments."""
    bps = ceiling.breakpoints[:]
    if not bps:
        return {'segments': [], 'h_max': 0}
    # Ensure we start at or before x_min
    segments = []
    for i, (bx, bh) in enumerate(bps):
        start = bx
        end = bps[i + 1][0] if i + 1 < len(bps) else x_max + 1
        segments.append({'x0': start, 'x1': end, 'h': bh})
    # Clip to [x_min, x_max]
    clipped = []
    for s in segments:
        if s['x1'] <= x_min or s['x0'] >= x_max:
            continue
        clipped.append({
            'x0': max(s['x0'], x_min),
            'x1': min(s['x1'], x_max),
            'h': s['h'],
        })
    h_max = max((s['h'] for s in clipped), default=0)
    return {'segments': clipped, 'h_max': h_max}


# ─────────────────────────────────────────────
# HTML emission
# ─────────────────────────────────────────────

def generate_html(
    case_dir: str,
    solution_files: List[str],
    labels: List[str],
    output_html: str,
) -> None:
    assert 1 <= len(solution_files) <= 2
    assert len(labels) == len(solution_files)

    warehouse = parse_warehouse(os.path.join(case_dir, 'warehouse.csv'))
    obstacles = parse_obstacles(os.path.join(case_dir, 'obstacles.csv'))
    bay_types = parse_bay_types(os.path.join(case_dir, 'types_of_bays.csv'))
    ceiling = parse_ceiling(os.path.join(case_dir, 'ceiling.csv'))
    bay_type_map = {bt.id: bt for bt in bay_types}

    usable_area_val = usable_area(warehouse, obstacles)
    solutions_payload = []
    for sf, lbl in zip(solution_files, labels):
        placed = _load_placed(sf, bay_type_map)
        payload = _solution_payload(placed, usable_area_val)
        payload['label'] = lbl
        solutions_payload.append(payload)

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

    ceiling_data = _ceiling_payload(ceiling, min_x, max_x)

    # Bay type catalog (for the legend)
    bay_type_list = [{
        'id': bt.id,
        'w': bt.width, 'd': bt.depth, 'h': bt.height,
        'gap': bt.gap, 'ld': bt.n_loads, 'pr': bt.price,
    } for bt in bay_types]

    case_name = os.path.basename(case_dir)
    compare_mode = len(solutions_payload) == 2

    html = _build_html(
        case_name=case_name,
        wh_coords=wh_coords,
        obs_data=obs_data,
        solutions=solutions_payload,
        ceiling=ceiling_data,
        bay_types=bay_type_list,
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
    ceiling: dict,
    bay_types: list,
    bbox: Tuple[float, float, float, float],
    compare_mode: bool,
) -> str:
    min_x, min_y, max_x, max_y = bbox
    subtitle = "Compare" if compare_mode else "Warehouse Optimizer"

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

    # Data blobs serialized once
    data_blob = {
        'case': case_name,
        'wh': wh_coords,
        'obs': obs_data,
        'solutions': solutions,
        'ceiling': ceiling,
        'bayTypes': bay_types,
        'bbox': {'x0': min_x, 'y0': min_y, 'x1': max_x, 'y1': max_y},
        'compare': compare_mode,
    }

    return _HTML_TEMPLATE.replace(
        '__CASE_NAME__', case_name,
    ).replace(
        '__SUBTITLE__', subtitle,
    ).replace(
        '__SWITCHER__', switcher_html,
    ).replace(
        '__DATA_JSON__', json.dumps(data_blob),
    )


# ─────────────────────────────────────────────
# HTML / JS template
# ─────────────────────────────────────────────
#
# Uses a regular triple-quoted string with __PLACEHOLDER__ tokens to avoid
# f-string / JS brace escaping hell.

_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__CASE_NAME__ — Warehouse Optimizer</title>

<!-- Three.js via import map (ESM) -->
<script type="importmap">
{
  "imports": {
    "three": "https://unpkg.com/three@0.160.0/build/three.module.js",
    "three/addons/": "https://unpkg.com/three@0.160.0/examples/jsm/"
  }
}
</script>

<style>
:root {
    --bg: #0e0f13;
    --bg-2: #14161c;
    --card: #1a1d25;
    --card-2: #22262f;
    --text: #e9eaf0;
    --text2: #8a8f9c;
    --text3: #5a5f6c;
    --border: #2a2e38;
    --accent: #6c9cff;
    --accent-2: #4a7bff;
    --red: #ff5d66;
    --green: #52d987;
    --orange: #ffb057;
    --yellow: #ffe066;
}
* { margin:0; padding:0; box-sizing:border-box; }
html, body { height: 100%; }
body {
    font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display',
                 'Inter', 'Helvetica Neue', Arial, sans-serif;
    background: var(--bg); color: var(--text);
    -webkit-font-smoothing: antialiased;
    overflow: hidden;
}

/* ── Sidebar ───────────────────────────────── */
.sidebar {
    position: fixed; left: 0; top: 0; bottom: 0; width: 300px;
    background: var(--bg-2);
    border-right: 1px solid var(--border);
    display: flex; flex-direction: column; z-index: 10;
}
.sidebar-header {
    padding: 22px 20px 14px;
    border-bottom: 1px solid var(--border);
    background: linear-gradient(180deg, #1a1d26 0%, var(--bg-2) 100%);
}
.sidebar-header h1 {
    font-size: 19px; font-weight: 600; letter-spacing: -0.3px;
    display: flex; align-items: center; gap: 10px;
}
.dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--accent);
    box-shadow: 0 0 12px var(--accent);
}
.sidebar-header .subtitle {
    font-size: 12px; color: var(--text2); margin-top: 4px;
    text-transform: uppercase; letter-spacing: 0.6px;
}

/* Stats grid */
.stats {
    display: grid; grid-template-columns: 1fr 1fr;
    gap: 1px; background: var(--border);
    border-bottom: 1px solid var(--border);
}
.stat { background: var(--bg-2); padding: 14px 16px; }
.stat .value {
    font-size: 22px; font-weight: 600; letter-spacing: -0.5px;
    font-variant-numeric: tabular-nums;
}
.stat .value.green { color: var(--green); }
.stat .value.orange { color: var(--orange); }
.stat .value.red { color: var(--red); }
.stat .label {
    font-size: 10px; color: var(--text2); text-transform: uppercase;
    letter-spacing: 0.7px; margin-top: 3px; font-weight: 500;
}
.stat .delta {
    font-size: 10px; margin-top: 5px;
    font-variant-numeric: tabular-nums;
    font-weight: 500;
}
.delta.up { color: var(--green); }
.delta.down { color: var(--red); }
.delta.flat { color: var(--text3); }

/* Controls */
.controls {
    padding: 14px 18px;
    border-bottom: 1px solid var(--border);
}
.controls h3 {
    font-size: 10px; text-transform: uppercase; letter-spacing: 0.7px;
    color: var(--text2); margin-bottom: 10px; font-weight: 600;
}
.toggle {
    display: flex; align-items: center; justify-content: space-between;
    font-size: 13px; color: var(--text); margin-bottom: 8px;
}
.toggle:last-child { margin-bottom: 0; }
.toggle input[type=checkbox] {
    appearance: none; -webkit-appearance: none;
    width: 34px; height: 20px; border-radius: 10px;
    background: var(--card-2); position: relative; cursor: pointer;
    transition: background 0.2s; border: 1px solid var(--border);
}
.toggle input[type=checkbox]:checked { background: var(--accent-2); border-color: var(--accent-2); }
.toggle input[type=checkbox]::after {
    content: ''; position: absolute; top: 1px; left: 1px;
    width: 16px; height: 16px; border-radius: 50%;
    background: #eeeff4; transition: transform 0.2s;
    box-shadow: 0 1px 3px rgba(0,0,0,0.4);
}
.toggle input[type=checkbox]:checked::after { transform: translateX(14px); }

/* Solution switcher */
.switch {
    display: flex;
    border: 1px solid var(--border); border-radius: 9px; overflow: hidden;
    background: var(--card);
}
.switch-btn {
    flex: 1; background: transparent; border: none; cursor: pointer;
    padding: 8px 14px; font-size: 13px; color: var(--text);
    transition: background 0.15s, color 0.15s;
    font-weight: 500;
    border-right: 1px solid var(--border);
}
.switch-btn:last-child { border-right: none; }
.switch-btn:hover { background: var(--card-2); }
.switch-btn.active {
    background: var(--accent-2); color: white;
}

/* Bay list */
.bay-list-header {
    padding: 14px 20px 6px;
    font-size: 10px; text-transform: uppercase; letter-spacing: 0.7px;
    color: var(--text2); font-weight: 600;
}
.bay-list {
    flex: 1; overflow-y: auto; padding: 0 10px 12px;
}
.bay-list::-webkit-scrollbar { width: 6px; }
.bay-list::-webkit-scrollbar-thumb { background: var(--card-2); border-radius: 3px; }
.bay-item {
    display: flex; align-items: center; gap: 10px;
    padding: 7px 10px; border-radius: 8px; cursor: pointer;
    transition: background 0.12s; font-size: 12.5px;
    margin-bottom: 1px;
}
.bay-item:hover { background: var(--card); }
.bay-item.active { background: rgba(108, 156, 255, 0.12); outline: 1px solid rgba(108,156,255,0.3); }
.bay-dot {
    width: 10px; height: 10px; border-radius: 3px; flex-shrink: 0;
    box-shadow: 0 0 6px rgba(0,0,0,0.3);
}
.bay-item .info { flex: 1; min-width: 0; }
.bay-item .info .name { font-weight: 500; }
.bay-item .info .detail { font-size: 10.5px; color: var(--text2); }

/* ── Main stage ────────────────────────────── */
.stage {
    margin-left: 300px; height: 100vh; position: relative;
    background:
        radial-gradient(ellipse at top, #1a1e2a 0%, var(--bg) 60%, #07080a 100%);
}

/* View mode toggle */
.view-toggle-wrap {
    position: absolute; top: 18px; left: 50%;
    transform: translateX(-50%);
    display: flex; gap: 10px;
    z-index: 20;
}
.view-toggle {
    display: flex; gap: 0;
    background: rgba(26, 29, 37, 0.9);
    border: 1px solid var(--border); border-radius: 10px;
    backdrop-filter: blur(14px); -webkit-backdrop-filter: blur(14px);
    overflow: hidden;
    box-shadow: 0 4px 16px rgba(0,0,0,0.35);
}
.vmode-btn {
    background: transparent; border: none; cursor: pointer;
    padding: 8px 20px; font-size: 13px; color: var(--text2);
    font-weight: 600; letter-spacing: 0.3px;
    transition: color 0.15s, background 0.15s;
    min-width: 70px;
}
.vmode-btn:hover { color: var(--text); }
.vmode-btn.active {
    background: var(--accent-2); color: white;
}

/* Catalog view */
#catalog {
    display: none; width: 100%; height: 100%;
    overflow-y: auto; padding: 70px 28px 28px;
}
.stage.mode-catalog #canvas2d,
.stage.mode-catalog #canvas3d { display: none; }
.stage.mode-catalog #catalog { display: block; }
.catalog-header {
    max-width: 1400px; margin: 0 auto 20px;
    display: flex; align-items: baseline; justify-content: space-between;
    gap: 16px;
}
.catalog-header h2 {
    font-size: 20px; font-weight: 600; letter-spacing: -0.3px;
}
.catalog-header .sub {
    font-size: 12px; color: var(--text2);
    text-transform: uppercase; letter-spacing: 0.6px;
}
.catalog-grid {
    max-width: 1400px; margin: 0 auto;
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 16px;
}
.catalog-card {
    background: var(--card); border: 1px solid var(--border);
    border-radius: 12px; overflow: hidden;
    transition: transform 0.15s, border-color 0.15s, box-shadow 0.15s;
    display: flex; flex-direction: column;
    cursor: pointer;
}
.catalog-card:hover {
    transform: translateY(-2px);
    border-color: rgba(108,156,255,0.4);
    box-shadow: 0 8px 24px rgba(0,0,0,0.4);
}
.catalog-preview {
    position: relative;
    height: 180px;
    background:
        radial-gradient(ellipse at top, #1f2533 0%, #10131a 70%);
    border-bottom: 1px solid var(--border);
}
.catalog-preview canvas { display: block; width: 100%; height: 100%; }
.catalog-chip {
    position: absolute; top: 10px; left: 10px;
    width: 12px; height: 12px; border-radius: 4px;
    box-shadow: 0 0 10px rgba(0,0,0,0.4);
}
.catalog-id {
    position: absolute; top: 8px; right: 10px;
    font-size: 11px; color: var(--text2);
    font-family: ui-monospace, 'SF Mono', Menlo, monospace;
    background: rgba(14,15,19,0.7);
    padding: 2px 7px; border-radius: 5px;
    border: 1px solid var(--border);
}
.catalog-hint {
    position: absolute; bottom: 8px; right: 10px;
    font-size: 10px; color: var(--text2);
    background: rgba(14,15,19,0.7);
    padding: 3px 8px; border-radius: 5px;
    border: 1px solid var(--border);
    opacity: 0; transition: opacity 0.15s;
}
.catalog-card:hover .catalog-hint { opacity: 1; }
.catalog-body {
    padding: 12px 14px 14px;
    display: flex; flex-direction: column; gap: 6px;
}
.catalog-title {
    font-size: 14px; font-weight: 600;
    display: flex; align-items: center; gap: 8px;
}
.catalog-dims {
    font-size: 12px; color: var(--text2);
    font-variant-numeric: tabular-nums;
    font-family: ui-monospace, 'SF Mono', Menlo, monospace;
}
.catalog-meta {
    display: grid; grid-template-columns: 1fr 1fr;
    gap: 4px 10px; margin-top: 4px;
    font-size: 11px; color: var(--text2);
}
.catalog-meta b { color: var(--text); font-weight: 500; }

/* Catalog 3D modal */
.modal-overlay {
    position: fixed; inset: 0;
    background: rgba(6, 7, 10, 0.72);
    backdrop-filter: blur(6px); -webkit-backdrop-filter: blur(6px);
    display: none; align-items: center; justify-content: center;
    z-index: 200;
    animation: fadeIn 0.18s ease;
}
.modal-overlay.open { display: flex; }
@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
.modal {
    width: min(880px, 92vw);
    height: min(620px, 88vh);
    background: var(--bg-2);
    border: 1px solid var(--border);
    border-radius: 14px;
    box-shadow: 0 24px 60px rgba(0,0,0,0.6);
    display: flex; flex-direction: column;
    overflow: hidden;
}
.modal-header {
    display: flex; align-items: center; gap: 12px;
    padding: 14px 18px;
    border-bottom: 1px solid var(--border);
}
.modal-header .chip {
    width: 14px; height: 14px; border-radius: 4px;
    box-shadow: 0 0 10px rgba(0,0,0,0.4);
}
.modal-header h3 {
    font-size: 16px; font-weight: 600; letter-spacing: -0.2px;
    flex: 1;
}
.modal-close {
    background: transparent; border: 1px solid var(--border);
    color: var(--text); font-size: 14px;
    width: 30px; height: 30px; border-radius: 8px;
    cursor: pointer; display: flex; align-items: center;
    justify-content: center;
    transition: background 0.15s, border-color 0.15s;
}
.modal-close:hover { background: var(--card-2); border-color: var(--accent-2); }
.modal-body {
    flex: 1; display: grid;
    grid-template-columns: 1fr 240px;
    min-height: 0;
}
.modal-3d {
    position: relative;
    background: radial-gradient(ellipse at top, #1a1e2a 0%, var(--bg) 70%);
}
.modal-3d canvas { display: block; }
.modal-info {
    border-left: 1px solid var(--border);
    padding: 18px 18px;
    display: flex; flex-direction: column; gap: 10px;
    font-size: 13px;
    background: var(--bg-2);
    overflow-y: auto;
}
.modal-info .row {
    display: flex; justify-content: space-between;
    padding: 6px 0;
    border-bottom: 1px solid var(--border);
}
.modal-info .row:last-child { border-bottom: none; }
.modal-info .row .k {
    color: var(--text2);
    text-transform: uppercase; letter-spacing: 0.5px;
    font-size: 10.5px; font-weight: 600;
    align-self: center;
}
.modal-info .row .v {
    color: var(--text); font-weight: 500;
    font-family: ui-monospace, 'SF Mono', Menlo, monospace;
    font-variant-numeric: tabular-nums;
}

/* Case badge */
.case-badge {
    position: absolute; top: 18px; right: 18px;
    background: rgba(26, 29, 37, 0.9);
    border: 1px solid var(--border);
    backdrop-filter: blur(14px); -webkit-backdrop-filter: blur(14px);
    padding: 6px 12px; border-radius: 8px;
    font-size: 12px; color: var(--text2);
    font-family: ui-monospace, 'SF Mono', Menlo, monospace;
    z-index: 20;
}

/* Canvases */
#canvas2d {
    display: block; width: 100%; height: 100%;
    cursor: grab;
}
#canvas2d:active { cursor: grabbing; }
#canvas3d {
    display: none; width: 100%; height: 100%;
}
.stage.mode-3d #canvas2d { display: none; }
.stage.mode-3d #canvas3d { display: block; }

/* Zoom/help controls */
.float-controls {
    position: absolute; bottom: 20px; right: 20px;
    display: flex; flex-direction: column; gap: 1px;
    background: var(--border); border-radius: 10px;
    overflow: hidden;
    box-shadow: 0 4px 14px rgba(0,0,0,0.4);
    z-index: 20;
}
.float-btn {
    width: 40px; height: 36px; background: rgba(26, 29, 37, 0.95);
    border: none; font-size: 16px; cursor: pointer;
    color: var(--text); display: flex; align-items: center;
    justify-content: center; transition: background 0.15s;
    backdrop-filter: blur(10px);
}
.float-btn:hover { background: var(--card-2); }

/* Tooltip */
.tooltip {
    position: fixed;
    background: rgba(26, 29, 37, 0.96);
    border: 1px solid var(--border);
    padding: 10px 14px; border-radius: 10px;
    font-size: 12px; pointer-events: none;
    display: none; z-index: 100;
    box-shadow: 0 4px 20px rgba(0,0,0,0.5);
    line-height: 1.55;
    color: var(--text);
    backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px);
    max-width: 260px;
}
.tooltip b { font-weight: 600; color: white; }
.tooltip .dim { color: var(--text2); }
.tooltip .hdr {
    display: flex; align-items: center; gap: 8px; margin-bottom: 4px;
}
.tooltip .chip {
    width: 10px; height: 10px; border-radius: 3px;
}

/* Legend (3D only) */
.legend {
    position: absolute; bottom: 20px; left: 20px;
    background: rgba(26, 29, 37, 0.9);
    border: 1px solid var(--border);
    backdrop-filter: blur(14px);
    padding: 10px 14px; border-radius: 10px;
    font-size: 11px; color: var(--text2);
    z-index: 20;
    display: flex; flex-direction: column; gap: 5px;
    box-shadow: 0 4px 14px rgba(0,0,0,0.4);
}
.legend .row { display: flex; align-items: center; gap: 8px; }
.legend .sq {
    width: 12px; height: 12px; border-radius: 3px;
    border: 1px solid rgba(255,255,255,0.1);
}

/* Loading hint */
.loading {
    position: absolute; inset: 0;
    display: flex; align-items: center; justify-content: center;
    color: var(--text2); font-size: 13px;
    pointer-events: none;
}
</style>
</head>
<body>

<div class="sidebar">
    <div class="sidebar-header">
        <h1><span class="dot"></span>__CASE_NAME__</h1>
        <div class="subtitle">__SUBTITLE__</div>
    </div>

    <div class="stats" id="statsGrid"></div>

__SWITCHER__

    <div class="controls">
        <h3>Display</h3>
        <div class="toggle"><span>Gaps / aisles</span><input type="checkbox" id="showGaps" checked></div>
        <div class="toggle"><span>Obstacles</span><input type="checkbox" id="showObstacles" checked></div>
        <div class="toggle"><span>Labels</span><input type="checkbox" id="showLabels" checked></div>
        <div class="toggle"><span>Grid</span><input type="checkbox" id="showGrid"></div>
        <div class="toggle" data-mode="3d"><span>Ceiling mesh</span><input type="checkbox" id="showCeiling" checked></div>
        <div class="toggle" data-mode="3d"><span>Axes helper</span><input type="checkbox" id="showAxes"></div>
    </div>

    <div class="bay-list-header" id="bayListHeader">Placed Bays</div>
    <div class="bay-list" id="bayList"></div>
</div>

<div class="stage mode-2d" id="stage">

    <div class="view-toggle-wrap">
        <div class="view-toggle" id="pageToggle">
            <button class="vmode-btn active" data-page="scene">Scene</button>
            <button class="vmode-btn" data-page="catalog">Catalog</button>
        </div>
        <div class="view-toggle" id="modeToggle">
            <button class="vmode-btn active" data-mode="2d">2D</button>
            <button class="vmode-btn" data-mode="3d">3D</button>
        </div>
    </div>

    <div class="case-badge" id="caseBadge">—</div>

    <canvas id="canvas2d"></canvas>
    <div id="canvas3d"></div>
    <div id="catalog">
        <div class="catalog-header">
            <div>
                <h2>Bay Type Catalog</h2>
                <div class="sub">All available bay types for this case · click any card for 3D view</div>
            </div>
            <div class="sub" id="catalogCount"></div>
        </div>
        <div class="catalog-grid" id="catalogGrid"></div>
    </div>

    <div class="legend" id="legend3d" style="display:none">
        <div class="row"><div class="sq" style="background:#3a6fe6"></div>Bay</div>
        <div class="row"><div class="sq" style="background:rgba(108,156,255,0.35);border-color:rgba(108,156,255,0.6)"></div>Gap / aisle</div>
        <div class="row"><div class="sq" style="background:#55606e"></div>Obstacle</div>
        <div class="row"><div class="sq" style="background:rgba(255,176,87,0.25);border-color:rgba(255,176,87,0.5)"></div>Ceiling</div>
    </div>

    <div class="float-controls" id="float2d">
        <button class="float-btn" id="zoomIn" title="Zoom in">+</button>
        <button class="float-btn" id="zoomOut" title="Zoom out">−</button>
        <button class="float-btn" id="zoomFit" title="Fit to view" style="font-size:13px">⌂</button>
    </div>

    <div class="float-controls" id="float3d" style="display:none;">
        <button class="float-btn" id="view3dTop" title="Top view">T</button>
        <button class="float-btn" id="view3dIso" title="Isometric">I</button>
        <button class="float-btn" id="view3dFront" title="Front view">F</button>
    </div>
</div>

<div class="modal-overlay" id="catalogModal">
    <div class="modal" role="dialog" aria-modal="true">
        <div class="modal-header">
            <div class="chip" id="modalChip"></div>
            <h3 id="modalTitle">Bay Type</h3>
            <button class="modal-close" id="modalClose" aria-label="Close">×</button>
        </div>
        <div class="modal-body">
            <div class="modal-3d" id="modal3d"></div>
            <div class="modal-info" id="modalInfo"></div>
        </div>
    </div>
</div>

<div class="tooltip" id="tooltip"></div>

<script type="module">
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const DATA = __DATA_JSON__;

const WH = DATA.wh;
const OBS = DATA.obs;
const SOLUTIONS = DATA.solutions;
const CEIL = DATA.ceiling;
const BAY_TYPES = DATA.bayTypes;
const B = DATA.bbox;
const COMPARE = DATA.compare;

// Cool palette for bay types — saturated, distinguishable in light and dark
const PALETTE = [
    '#6c9cff', '#52d987', '#ffb057', '#ff5d66', '#c78bff', '#5ac8fa',
    '#ffd166', '#06d6a8', '#ef798a', '#8ac6d1', '#f7a072', '#b28dff',
    '#f4978e', '#6dd3ce', '#ffd670', '#e9ff70', '#ffa1b7', '#70d6ff',
];
function colorFor(tid) { return PALETTE[tid % PALETTE.length]; }
function hexToRgb(hex) {
    const h = hex.replace('#', '');
    return {
        r: parseInt(h.slice(0, 2), 16),
        g: parseInt(h.slice(2, 4), 16),
        b: parseInt(h.slice(4, 6), 16),
    };
}

let activeIdx = 0;
let BAYS = SOLUTIONS[activeIdx].bays;

let viewMode = '2d';  // '2d' | '3d'

/* ─────────────────────────────────────────────
 * 2D VIEW
 * ───────────────────────────────────────────── */
const canvas2d = document.getElementById('canvas2d');
const ctx = canvas2d.getContext('2d');
const tip = document.getElementById('tooltip');
let sc = 1, ox = 0, oy = 0;
let drag = false, dx0, dy0;
let hov2d = -1, sel = -1;

function resize2d() {
    const r = canvas2d.parentElement.getBoundingClientRect();
    canvas2d.width = r.width * devicePixelRatio;
    canvas2d.height = r.height * devicePixelRatio;
    canvas2d.style.width = r.width + 'px';
    canvas2d.style.height = r.height + 'px';
    ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
    fit2d();
    draw2d();
}

function fit2d() {
    const cw = canvas2d.width / devicePixelRatio;
    const ch = canvas2d.height / devicePixelRatio;
    const pad = 50;
    const sx = (cw - pad * 2) / (B.x1 - B.x0);
    const sy = (ch - pad * 2) / (B.y1 - B.y0);
    sc = Math.min(sx, sy);
    ox = (cw - (B.x1 - B.x0) * sc) / 2 - B.x0 * sc;
    oy = (ch - (B.y1 - B.y0) * sc) / 2 - B.y0 * sc;
}

function ts(x, y) {
    const ch = canvas2d.height / devicePixelRatio;
    return [x * sc + ox, ch - (y * sc + oy)];
}
function tw(sx, sy) {
    const ch = canvas2d.height / devicePixelRatio;
    return [(sx - ox) / sc, (ch - sy - oy) / sc];
}

function roundRect(x, y, w, h, r) {
    r = Math.min(r, Math.abs(w) / 2, Math.abs(h) / 2);
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.lineTo(x + w - r, y); ctx.arcTo(x + w, y, x + w, y + r, r);
    ctx.lineTo(x + w, y + h - r); ctx.arcTo(x + w, y + h, x + w - r, y + h, r);
    ctx.lineTo(x + r, y + h); ctx.arcTo(x, y + h, x, y + h - r, r);
    ctx.lineTo(x, y + r); ctx.arcTo(x, y, x + r, y, r);
    ctx.closePath();
}

function gapSide(rot) {
    // Which local edge of the bay the gap sticks out from.
    // Returns one of 'N','S','E','W' for convenience.
    if (rot === 0) return 'N';
    if (rot === 180) return 'S';
    if (rot === 90) return 'W';
    if (rot === 270) return 'E';
    return 'N';
}

function draw2d() {
    const cw = canvas2d.width / devicePixelRatio;
    const ch = canvas2d.height / devicePixelRatio;
    ctx.clearRect(0, 0, cw, ch);

    const sGap = document.getElementById('showGaps').checked;
    const sObs = document.getElementById('showObstacles').checked;
    const sLbl = document.getElementById('showLabels').checked;
    const sGrid = document.getElementById('showGrid').checked;

    // Background grid
    if (sGrid) {
        const span = Math.max(B.x1 - B.x0, B.y1 - B.y0);
        const step = Math.pow(10, Math.floor(Math.log10(span)) - 1);
        ctx.strokeStyle = 'rgba(255,255,255,0.04)';
        ctx.lineWidth = 1;
        for (let gx = Math.floor(B.x0 / step) * step; gx <= B.x1; gx += step) {
            const [sx] = ts(gx, 0);
            ctx.beginPath(); ctx.moveTo(sx, 0); ctx.lineTo(sx, ch); ctx.stroke();
        }
        for (let gy = Math.floor(B.y0 / step) * step; gy <= B.y1; gy += step) {
            const [, sy] = ts(0, gy);
            ctx.beginPath(); ctx.moveTo(0, sy); ctx.lineTo(cw, sy); ctx.stroke();
        }
    }

    // Warehouse fill + outline
    ctx.beginPath();
    for (let i = 0; i < WH.length; i++) {
        const [sx, sy] = ts(WH[i][0], WH[i][1]);
        i === 0 ? ctx.moveTo(sx, sy) : ctx.lineTo(sx, sy);
    }
    ctx.closePath();
    // Subtle gradient floor
    const grad = ctx.createLinearGradient(0, 0, 0, ch);
    grad.addColorStop(0, '#1c212d');
    grad.addColorStop(1, '#151821');
    ctx.fillStyle = grad;
    ctx.fill();
    ctx.strokeStyle = '#3a4050';
    ctx.lineWidth = 1.5;
    ctx.stroke();

    // Obstacles
    if (sObs) {
        for (const o of OBS) {
            const [sx, sy] = ts(o.x, o.y + o.h);
            const w = o.w * sc, h = o.h * sc;
            const r = Math.min(3, w / 4, h / 4);
            roundRect(sx, sy, w, h, r);
            ctx.fillStyle = 'rgba(255, 93, 102, 0.18)';
            ctx.fill();
            ctx.strokeStyle = 'rgba(255, 93, 102, 0.55)';
            ctx.lineWidth = 1;
            ctx.stroke();
            // Diagonal hatching (subtle)
            ctx.save();
            ctx.beginPath(); roundRect(sx, sy, w, h, r); ctx.clip();
            ctx.strokeStyle = 'rgba(255, 93, 102, 0.18)';
            ctx.lineWidth = 0.7;
            for (let dy = -h; dy < w + h; dy += 6) {
                ctx.beginPath();
                ctx.moveTo(sx + dy, sy); ctx.lineTo(sx + dy + h, sy + h);
                ctx.stroke();
            }
            ctx.restore();
        }
    }

    // Gaps (drawn under bays)
    if (sGap) {
        for (const b of BAYS) {
            const [gsx, gsy] = ts(b.gx, b.gy + b.gh);
            const gw = b.gw * sc, gh = b.gh * sc;
            ctx.fillStyle = 'rgba(108, 156, 255, 0.10)';
            ctx.fillRect(gsx, gsy, gw, gh);
            ctx.strokeStyle = 'rgba(108, 156, 255, 0.35)';
            ctx.lineWidth = 0.5;
            ctx.setLineDash([3, 3]);
            ctx.strokeRect(gsx, gsy, gw, gh);
            ctx.setLineDash([]);
        }
    }

    // Bays
    for (let i = 0; i < BAYS.length; i++) {
        const b = BAYS[i];
        const [sx, sy] = ts(b.x, b.y + b.d);
        const w = b.w * sc, h = b.d * sc;
        const col = colorFor(b.tid);
        const isH = i === hov2d;
        const isS = i === sel;
        const r = Math.min(2, w / 4, h / 4);

        if (isH || isS) {
            ctx.save();
            ctx.shadowColor = col + 'cc';
            ctx.shadowBlur = 16;
        }

        // Fill with slight vertical gradient
        const g = ctx.createLinearGradient(sx, sy, sx, sy + h);
        const rgb = hexToRgb(col);
        const a = isH || isS ? 0.95 : 0.72;
        g.addColorStop(0, `rgba(${rgb.r},${rgb.g},${rgb.b},${a})`);
        g.addColorStop(1, `rgba(${Math.floor(rgb.r * 0.7)},${Math.floor(rgb.g * 0.7)},${Math.floor(rgb.b * 0.7)},${a})`);
        roundRect(sx, sy, w, h, r);
        ctx.fillStyle = g;
        ctx.fill();
        ctx.strokeStyle = isH || isS ? col : 'rgba(255,255,255,0.12)';
        ctx.lineWidth = isH || isS ? 1.5 : 0.8;
        ctx.stroke();

        if (isH || isS) ctx.restore();

        // Gap side hint (thin colored edge)
        const side = gapSide(b.rot);
        ctx.strokeStyle = 'rgba(108, 156, 255, 0.8)';
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        if (side === 'N') { ctx.moveTo(sx, sy); ctx.lineTo(sx + w, sy); }
        if (side === 'S') { ctx.moveTo(sx, sy + h); ctx.lineTo(sx + w, sy + h); }
        if (side === 'W') { ctx.moveTo(sx, sy); ctx.lineTo(sx, sy + h); }
        if (side === 'E') { ctx.moveTo(sx + w, sy); ctx.lineTo(sx + w, sy + h); }
        ctx.stroke();

        if (sLbl && w > 22 && h > 14) {
            ctx.fillStyle = 'rgba(255,255,255,0.95)';
            const fs = Math.min(11, Math.max(8, Math.min(w, h) * 0.4));
            ctx.font = `600 ${fs}px -apple-system, sans-serif`;
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(b.tid, sx + w / 2, sy + h / 2);
        }
    }

    // Warehouse outline (on top)
    ctx.beginPath();
    for (let i = 0; i < WH.length; i++) {
        const [sx, sy] = ts(WH[i][0], WH[i][1]);
        i === 0 ? ctx.moveTo(sx, sy) : ctx.lineTo(sx, sy);
    }
    ctx.closePath();
    ctx.strokeStyle = '#8a93a8';
    ctx.lineWidth = 1.8;
    ctx.stroke();
}

/* Stats formatters */
function fmtCov(c) { return (c * 100).toFixed(0) + '%'; }
function fmtScore(s) { return s.toFixed(0); }
function fmtPPL(p) { return p.toFixed(0); }

function deltaHtml(cur, other, lowerIsBetter, fmt) {
    if (other === null || other === undefined) return '';
    const d = cur - other;
    if (Math.abs(d) < 0.5 && fmt === fmtScore) return `<div class="delta flat">±0 vs ${fmt(other)}</div>`;
    let cls = 'flat';
    if (d > 0) cls = lowerIsBetter ? 'down' : 'up';
    if (d < 0) cls = lowerIsBetter ? 'up' : 'down';
    const sign = d > 0 ? '+' : '';
    if (fmt === fmtCov) {
        const dd = d * 100;
        const s = dd > 0 ? '+' : '';
        return `<div class="delta ${cls}">${s}${dd.toFixed(1)}pp vs ${fmt(other)}</div>`;
    }
    return `<div class="delta ${cls}">${sign}${fmt(d)} vs ${fmt(other)}</div>`;
}

function renderStats() {
    const s = SOLUTIONS[activeIdx].stats;
    const other = COMPARE ? SOLUTIONS[1 - activeIdx].stats : null;
    const covClass = s.coverage > 0.6 ? 'green' : s.coverage > 0.4 ? 'orange' : 'red';
    document.getElementById('statsGrid').innerHTML = `
        <div class="stat">
            <div class="value">${s.n_bays}</div>
            <div class="label">Bays</div>
            ${other ? deltaHtml(s.n_bays, other.n_bays, false, v => v.toFixed(0)) : ''}
        </div>
        <div class="stat">
            <div class="value ${covClass}">${fmtCov(s.coverage)}</div>
            <div class="label">Coverage</div>
            ${other ? deltaHtml(s.coverage, other.coverage, false, fmtCov) : ''}
        </div>
        <div class="stat">
            <div class="value">${fmtScore(s.score)}</div>
            <div class="label">Score Q</div>
            ${other ? deltaHtml(s.score, other.score, true, fmtScore) : ''}
        </div>
        <div class="stat">
            <div class="value">${fmtPPL(s.price_per_load)}</div>
            <div class="label">Price / Load</div>
            ${other ? deltaHtml(s.price_per_load, other.price_per_load, true, fmtPPL) : ''}
        </div>
    `;
    document.getElementById('caseBadge').textContent =
        `${DATA.case}  ·  ${s.n_bays} bays  ·  Q=${fmtScore(s.score)}  ·  cov=${fmtCov(s.coverage)}`;
}

function renderBayList() {
    const list = document.getElementById('bayList');
    list.innerHTML = '';
    document.getElementById('bayListHeader').textContent =
        `Placed Bays (${BAYS.length})`;
    BAYS.forEach((b, i) => {
        const el = document.createElement('div');
        el.className = 'bay-item';
        el.dataset.idx = i;
        el.innerHTML = `
            <div class="bay-dot" style="background:${colorFor(b.tid)}"></div>
            <div class="info">
                <div class="name">Type ${b.tid} <span style="color:var(--text3)">#${i}</span></div>
                <div class="detail">${b.w}×${b.d} @ (${Math.round(b.x)}, ${Math.round(b.y)}) ${b.rot}°</div>
            </div>
        `;
        el.addEventListener('mouseenter', () => { hov2d = i; if (view3d) view3d.setHover(i); draw2d(); });
        el.addEventListener('mouseleave', () => { hov2d = -1; if (view3d) view3d.setHover(-1); draw2d(); });
        el.addEventListener('click', () => {
            sel = sel === i ? -1 : i;
            document.querySelectorAll('.bay-item').forEach(e => e.classList.remove('active'));
            if (sel >= 0) {
                el.classList.add('active');
                if (view3d && viewMode === '3d') view3d.focusBay(i);
            }
            draw2d();
            if (view3d) view3d.setSelected(sel);
        });
        list.appendChild(el);
    });
}

function switchSolution(idx) {
    activeIdx = idx;
    BAYS = SOLUTIONS[activeIdx].bays;
    hov2d = -1;
    sel = -1;
    renderStats();
    renderBayList();
    draw2d();
    if (view3d) view3d.rebuildBays();
}

if (COMPARE) {
    document.querySelectorAll('.switch-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.switch-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            switchSolution(parseInt(btn.dataset.sol));
        });
    });
    document.addEventListener('keydown', (e) => {
        if (document.activeElement.tagName === 'INPUT') return;
        if (e.key === '1') {
            switchSolution(0);
            document.querySelectorAll('.switch-btn').forEach((b, i) => b.classList.toggle('active', i === 0));
        } else if (e.key === '2') {
            switchSolution(1);
            document.querySelectorAll('.switch-btn').forEach((b, i) => b.classList.toggle('active', i === 1));
        } else if (e.key === 'Tab') {
            e.preventDefault();
            const next = 1 - activeIdx;
            switchSolution(next);
            document.querySelectorAll('.switch-btn').forEach((b, i) => b.classList.toggle('active', i === next));
        }
    });
}

/* ── 2D canvas interaction ─────────────── */
canvas2d.addEventListener('wheel', (e) => {
    e.preventDefault();
    const rect = canvas2d.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const z = e.deltaY < 0 ? 1.12 : 1 / 1.12;
    const [wx, wy] = tw(mx, my);
    sc *= z;
    const ch = canvas2d.height / devicePixelRatio;
    ox = mx - wx * sc;
    oy = -(my - ch) - wy * sc;
    draw2d();
}, { passive: false });

canvas2d.addEventListener('mousedown', (e) => { drag = true; dx0 = e.clientX; dy0 = e.clientY; });
canvas2d.addEventListener('mousemove', (e) => {
    if (drag) {
        ox += e.clientX - dx0; oy -= e.clientY - dy0;
        dx0 = e.clientX; dy0 = e.clientY;
        draw2d(); return;
    }
    const rect = canvas2d.getBoundingClientRect();
    const [wx, wy] = tw(e.clientX - rect.left, e.clientY - rect.top);
    let f = -1;
    for (let i = BAYS.length - 1; i >= 0; i--) {
        const b = BAYS[i];
        if (wx >= b.x && wx <= b.x + b.w && wy >= b.y && wy <= b.y + b.d) { f = i; break; }
    }
    if (f !== hov2d) { hov2d = f; draw2d(); if (view3d) view3d.setHover(f); }
    showTooltip(f, e);
});
canvas2d.addEventListener('mouseup', () => drag = false);
canvas2d.addEventListener('mouseleave', () => { drag = false; tip.style.display = 'none'; });
canvas2d.addEventListener('click', (e) => {
    if (hov2d >= 0) {
        sel = sel === hov2d ? -1 : hov2d;
        document.querySelectorAll('.bay-item').forEach((el, i) => el.classList.toggle('active', i === sel));
        if (sel >= 0) document.querySelector(`.bay-item[data-idx="${sel}"]`)?.scrollIntoView({ block: 'nearest' });
        draw2d();
        if (view3d) view3d.setSelected(sel);
    }
});

function showTooltip(idx, e) {
    if (idx < 0) { tip.style.display = 'none'; return; }
    const b = BAYS[idx];
    tip.style.display = 'block';
    tip.style.left = (e.clientX + 14) + 'px';
    tip.style.top = (e.clientY + 14) + 'px';
    tip.innerHTML = `
        <div class="hdr">
            <div class="chip" style="background:${colorFor(b.tid)}"></div>
            <b>Type ${b.tid}</b> <span class="dim">#${idx}</span>
        </div>
        ${b.w} × ${b.d} × ${b.h} mm<br>
        <span class="dim">Position</span> (${Math.round(b.x)}, ${Math.round(b.y)})  ${b.rot}°<br>
        <span class="dim">Gap</span> ${b.gap} mm<br>
        <span class="dim">Price</span> ${b.pr}  <span class="dim">Loads</span> ${b.ld}
    `;
}

document.getElementById('zoomIn').addEventListener('click', () => {
    const ch = canvas2d.height / devicePixelRatio;
    const cw = canvas2d.width / devicePixelRatio;
    const [wx, wy] = tw(cw / 2, ch / 2);
    sc *= 1.3;
    ox = cw / 2 - wx * sc;
    oy = -(ch / 2 - ch) - wy * sc;
    draw2d();
});
document.getElementById('zoomOut').addEventListener('click', () => {
    const ch = canvas2d.height / devicePixelRatio;
    const cw = canvas2d.width / devicePixelRatio;
    const [wx, wy] = tw(cw / 2, ch / 2);
    sc /= 1.3;
    ox = cw / 2 - wx * sc;
    oy = -(ch / 2 - ch) - wy * sc;
    draw2d();
});
document.getElementById('zoomFit').addEventListener('click', () => { fit2d(); draw2d(); });

['showGaps', 'showObstacles', 'showLabels', 'showGrid'].forEach(id =>
    document.getElementById(id).addEventListener('change', () => {
        draw2d();
        if (view3d) view3d.updateVisibility();
    }));
document.getElementById('showCeiling').addEventListener('change', () => { if (view3d) view3d.updateVisibility(); });
document.getElementById('showAxes').addEventListener('change', () => { if (view3d) view3d.updateVisibility(); });

/* ─────────────────────────────────────────────
 * 3D VIEW
 * ───────────────────────────────────────────── */
class ThreeView {
    constructor(container) {
        this.container = container;
        this.scene = new THREE.Scene();
        this.scene.background = null;
        // No fog — avoids fading geometry when zooming out to see the whole warehouse.

        // Units are millimetres — typical warehouse is tens of thousands.
        const aspect = container.clientWidth / Math.max(1, container.clientHeight);
        this.camera = new THREE.PerspectiveCamera(45, aspect, 100, 200000);

        this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
        this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
        this.renderer.setSize(container.clientWidth, container.clientHeight);
        this.renderer.outputColorSpace = THREE.SRGBColorSpace;
        this.renderer.shadowMap.enabled = true;
        this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
        container.appendChild(this.renderer.domElement);

        // Center the scene around origin — shift world coords by (-cx, -cy).
        this.cx = (B.x0 + B.x1) / 2;
        this.cy = (B.y0 + B.y1) / 2;
        this.wx = B.x1 - B.x0;
        this.wy = B.y1 - B.y0;

        this.controls = new OrbitControls(this.camera, this.renderer.domElement);
        this.controls.enableDamping = true;
        this.controls.dampingFactor = 0.08;
        this.controls.minDistance = 500;
        this.controls.maxDistance = 120000;
        this.controls.maxPolarAngle = Math.PI / 2 - 0.02;

        this._setIso();

        // Lights
        const amb = new THREE.AmbientLight(0xffffff, 0.55);
        this.scene.add(amb);

        const hemi = new THREE.HemisphereLight(0xbfd4ff, 0x20222a, 0.6);
        this.scene.add(hemi);

        const dir = new THREE.DirectionalLight(0xffffff, 0.9);
        dir.position.set(this.wx * 0.4, this.wy * 0.3, 15000);
        dir.castShadow = true;
        dir.shadow.mapSize.set(2048, 2048);
        const sh = Math.max(this.wx, this.wy);
        dir.shadow.camera.left = -sh;
        dir.shadow.camera.right = sh;
        dir.shadow.camera.top = sh;
        dir.shadow.camera.bottom = -sh;
        dir.shadow.camera.near = 100;
        dir.shadow.camera.far = 60000;
        dir.shadow.bias = -0.0005;
        this.scene.add(dir);
        this.scene.add(dir.target);
        dir.target.position.set(0, 0, 0);

        // Subtle rim from the opposite side
        const dir2 = new THREE.DirectionalLight(0x8ab8ff, 0.28);
        dir2.position.set(-this.wx * 0.3, -this.wy * 0.4, 10000);
        this.scene.add(dir2);

        // Raycaster
        this.raycaster = new THREE.Raycaster();
        this.mouse = new THREE.Vector2();

        // Groups
        this.floorGroup = new THREE.Group(); this.scene.add(this.floorGroup);
        this.obstacleGroup = new THREE.Group(); this.scene.add(this.obstacleGroup);
        this.gapGroup = new THREE.Group(); this.scene.add(this.gapGroup);
        this.bayGroup = new THREE.Group(); this.scene.add(this.bayGroup);
        this.ceilingGroup = new THREE.Group(); this.scene.add(this.ceilingGroup);
        this.axesHelper = new THREE.AxesHelper(Math.max(this.wx, this.wy) * 0.15);
        this.axesHelper.position.set(-this.wx / 2 - 500, -this.wy / 2 - 500, 0);
        this.axesHelper.visible = false;
        this.scene.add(this.axesHelper);

        this._buildStaticScene();
        this.rebuildBays();

        this._hoverIdx = -1;
        this._selIdx = -1;
        this._bayMeshes = [];

        this.renderer.domElement.addEventListener('pointermove', (e) => this._onPointerMove(e));
        this.renderer.domElement.addEventListener('click', (e) => this._onClick(e));

        this._running = true;
        this._animate();
        window.addEventListener('resize', () => this._onResize());
    }

    // World coords (x, y, z) are in warehouse mm; y is depth, z is height.
    // We rotate X-Y plane to XZ-facing by mapping (x, y, z) -> (x - cx, z, -(y - cy))
    // i.e. scene Y = warehouse height (Z), scene Z = -(warehouse Y). This keeps
    // warehouse X on the scene's X axis and gives a natural "floor" plane at y=0.
    w2s(x, y, z = 0) {
        return [x - this.cx, z, -(y - this.cy)];
    }

    _setIso() {
        const d = Math.max(this.wx, this.wy) * 1.35;
        this.camera.position.set(d * 0.6, d * 0.55, d * 0.8);
        this.camera.lookAt(0, 0, 0);
        this.controls.target.set(0, 0, 0);
        this.controls.update();
    }

    setTopView() {
        const d = Math.max(this.wx, this.wy) * 1.35;
        this.camera.position.set(0.1, d, 0.1);
        this.controls.target.set(0, 0, 0);
        this.controls.update();
    }
    setFrontView() {
        const d = Math.max(this.wx, this.wy) * 1.6;
        this.camera.position.set(0, Math.min(d * 0.35, 12000), d * 0.9);
        this.controls.target.set(0, 0, 0);
        this.controls.update();
    }
    setIso() { this._setIso(); }

    _ceilingHeightInRange(x0, x1) {
        // Mimic solver.Ceiling.min_height_in_range
        if (!CEIL.segments.length) return 5000;
        let minH = Infinity;
        for (const s of CEIL.segments) {
            if (s.x1 <= x0 || s.x0 >= x1) continue;
            minH = Math.min(minH, s.h);
        }
        if (!isFinite(minH)) {
            // Fallback: height at x0
            let h = CEIL.segments[0].h;
            for (const s of CEIL.segments) if (s.x0 <= x0) h = s.h;
            return h;
        }
        return minH;
    }

    _buildStaticScene() {
        // Floor polygon (warehouse) via ShapeGeometry
        const shape = new THREE.Shape();
        for (let i = 0; i < WH.length; i++) {
            const [sx, , sz] = this.w2s(WH[i][0], WH[i][1], 0);
            if (i === 0) shape.moveTo(sx, -sz); else shape.lineTo(sx, -sz);
        }
        shape.closePath();
        const floorGeo = new THREE.ShapeGeometry(shape);
        const floorMat = new THREE.MeshStandardMaterial({
            color: 0x1e2330, roughness: 0.95, metalness: 0.0,
        });
        const floor = new THREE.Mesh(floorGeo, floorMat);
        floor.rotation.x = -Math.PI / 2;  // shape's Y maps to -Z; flip to floor plane
        floor.position.y = 0;
        floor.receiveShadow = true;
        this.floorGroup.add(floor);

        // Grid helper on floor
        const gridSize = Math.max(this.wx, this.wy);
        const step = Math.pow(10, Math.floor(Math.log10(gridSize))) / 2;
        const divisions = Math.max(4, Math.round(gridSize / step));
        const grid = new THREE.GridHelper(gridSize * 1.1, divisions, 0x3a4050, 0x2a2e38);
        grid.position.y = 0.5;
        grid.material.opacity = 0.35;
        grid.material.transparent = true;
        this.floorGroup.add(grid);

        // Warehouse outline — coherent with the floor's coordinate system.
        // The floor is built as a ShapeGeometry and rotated by -PI/2 around X,
        // so a shape point (sx, y - cy) lands at world (sx, 0, -(y - cy)).
        // The outline therefore must use the same world Z as w2s returns (=sz).
        const points = [];
        for (let i = 0; i < WH.length; i++) {
            const [sx, , sz] = this.w2s(WH[i][0], WH[i][1], 0);
            points.push(new THREE.Vector3(sx, 5, sz));
        }
        points.push(points[0].clone());
        const outlineGeo = new THREE.BufferGeometry().setFromPoints(points);
        const outline = new THREE.Line(outlineGeo, new THREE.LineBasicMaterial({
            color: 0x8a93a8, linewidth: 2,
        }));
        this.floorGroup.add(outline);

        // Obstacles — dark extruded columns up to local ceiling
        for (const o of OBS) {
            const h = this._ceilingHeightInRange(o.x, o.x + o.w);
            const geo = new THREE.BoxGeometry(o.w, h, o.h);
            const mat = new THREE.MeshStandardMaterial({
                color: 0x55606e, roughness: 0.85, metalness: 0.1,
            });
            const m = new THREE.Mesh(geo, mat);
            const [sx, , sz] = this.w2s(o.x + o.w / 2, o.y + o.h / 2, 0);
            m.position.set(sx, h / 2, sz);
            m.castShadow = true;
            m.receiveShadow = true;
            this.obstacleGroup.add(m);

            // Red-tinted outline at top to stay coherent with 2D
            const edge = new THREE.LineSegments(
                new THREE.EdgesGeometry(geo),
                new THREE.LineBasicMaterial({ color: 0xff5d66, transparent: true, opacity: 0.4 })
            );
            edge.position.copy(m.position);
            this.obstacleGroup.add(edge);
        }

        // Ceiling mesh — piecewise box per segment, translucent
        for (const s of CEIL.segments) {
            const w = s.x1 - s.x0;
            if (w <= 0) continue;
            // Ceiling only above the warehouse footprint, but we can
            // render it across [y0, y1] and let it be visually clipped.
            const d = this.wy;
            const thickness = 40;
            const geo = new THREE.BoxGeometry(w, thickness, d);
            const mat = new THREE.MeshStandardMaterial({
                color: 0xffb057,
                transparent: true,
                opacity: 0.13,
                side: THREE.DoubleSide,
                roughness: 1.0,
            });
            const m = new THREE.Mesh(geo, mat);
            const [sx, , sz] = this.w2s(s.x0 + w / 2, (B.y0 + B.y1) / 2, 0);
            m.position.set(sx, s.h, sz);
            this.ceilingGroup.add(m);

            // Top edge outline for clarity
            const edge = new THREE.LineSegments(
                new THREE.EdgesGeometry(geo),
                new THREE.LineBasicMaterial({ color: 0xffb057, transparent: true, opacity: 0.35 })
            );
            edge.position.copy(m.position);
            this.ceilingGroup.add(edge);
        }
    }

    rebuildBays() {
        // Clear existing bay + gap meshes
        while (this.bayGroup.children.length) {
            const m = this.bayGroup.children.pop();
            m.geometry?.dispose();
            m.material?.dispose?.();
        }
        while (this.gapGroup.children.length) {
            const m = this.gapGroup.children.pop();
            m.geometry?.dispose();
            m.material?.dispose?.();
        }
        this._bayMeshes = [];

        for (let i = 0; i < BAYS.length; i++) {
            const b = BAYS[i];
            const colHex = colorFor(b.tid);
            const col = new THREE.Color(colHex);

            // Bay (extruded rectangle)
            const geo = new THREE.BoxGeometry(b.w, b.h, b.d);
            const mat = new THREE.MeshStandardMaterial({
                color: col,
                roughness: 0.42,
                metalness: 0.12,
                emissive: col.clone().multiplyScalar(0.05),
            });
            const mesh = new THREE.Mesh(geo, mat);
            const [sx, , sz] = this.w2s(b.x + b.w / 2, b.y + b.d / 2, 0);
            mesh.position.set(sx, b.h / 2, sz);
            mesh.castShadow = true;
            mesh.receiveShadow = true;
            mesh.userData = { idx: i, baseColor: col.clone(), baseEmissive: mat.emissive.clone() };
            this.bayGroup.add(mesh);
            this._bayMeshes.push(mesh);

            // Outline (subtle)
            const edge = new THREE.LineSegments(
                new THREE.EdgesGeometry(geo),
                new THREE.LineBasicMaterial({
                    color: 0x000000, transparent: true, opacity: 0.22,
                })
            );
            edge.position.copy(mesh.position);
            this.bayGroup.add(edge);

            // Gap as translucent floor slab
            const gapGeo = new THREE.BoxGeometry(b.gw, 20, b.gh);
            const gapMat = new THREE.MeshStandardMaterial({
                color: 0x6c9cff,
                transparent: true,
                opacity: 0.22,
                roughness: 1.0,
            });
            const gm = new THREE.Mesh(gapGeo, gapMat);
            const [gsx, , gsz] = this.w2s(b.gx + b.gw / 2, b.gy + b.gh / 2, 0);
            gm.position.set(gsx, 10, gsz);
            this.gapGroup.add(gm);

            // Dashed outline on the floor to show gap extents
            const gapEdge = new THREE.LineSegments(
                new THREE.EdgesGeometry(new THREE.BoxGeometry(b.gw, 0.1, b.gh)),
                new THREE.LineDashedMaterial({
                    color: 0x6c9cff, dashSize: 120, gapSize: 80,
                    transparent: true, opacity: 0.55,
                })
            );
            gapEdge.position.set(gsx, 12, gsz);
            gapEdge.computeLineDistances();
            this.gapGroup.add(gapEdge);
        }
        this.updateVisibility();
    }

    updateVisibility() {
        const sGap = document.getElementById('showGaps').checked;
        const sObs = document.getElementById('showObstacles').checked;
        const sCeil = document.getElementById('showCeiling').checked;
        const sAx = document.getElementById('showAxes').checked;
        const sGrid = document.getElementById('showGrid').checked;
        this.gapGroup.visible = sGap;
        this.obstacleGroup.visible = sObs;
        this.ceilingGroup.visible = sCeil;
        this.axesHelper.visible = sAx;
        // Toggle grid helper
        this.floorGroup.children.forEach(c => {
            if (c.isGridHelper) c.visible = sGrid;
        });
    }

    setHover(idx) {
        this._hoverIdx = idx;
        this._refreshHighlights();
    }

    setSelected(idx) {
        this._selIdx = idx;
        this._refreshHighlights();
    }

    _refreshHighlights() {
        for (let i = 0; i < this._bayMeshes.length; i++) {
            const m = this._bayMeshes[i];
            const isH = i === this._hoverIdx;
            const isS = i === this._selIdx;
            if (isH || isS) {
                m.material.emissive = m.userData.baseColor.clone().multiplyScalar(isS ? 0.5 : 0.3);
                m.scale.setScalar(1.005);
            } else {
                m.material.emissive = m.userData.baseEmissive.clone();
                m.scale.setScalar(1.0);
            }
        }
    }

    focusBay(idx) {
        if (idx < 0 || idx >= BAYS.length) return;
        const b = BAYS[idx];
        const [sx, , sz] = this.w2s(b.x + b.w / 2, b.y + b.d / 2, 0);
        const target = new THREE.Vector3(sx, b.h / 2, sz);
        const d = Math.max(b.w, b.d, b.h) * 3 + 1500;
        const camTarget = new THREE.Vector3(sx + d * 0.7, target.y + d * 0.6, sz + d * 0.7);
        this._animateCamera(camTarget, target, 600);
    }

    _animateCamera(toPos, toTarget, duration) {
        const fromPos = this.camera.position.clone();
        const fromTarget = this.controls.target.clone();
        const start = performance.now();
        const step = () => {
            const t = Math.min(1, (performance.now() - start) / duration);
            const k = 1 - Math.pow(1 - t, 3);  // easeOutCubic
            this.camera.position.lerpVectors(fromPos, toPos, k);
            this.controls.target.lerpVectors(fromTarget, toTarget, k);
            this.controls.update();
            if (t < 1) requestAnimationFrame(step);
        };
        requestAnimationFrame(step);
    }

    _onPointerMove(e) {
        const rect = this.renderer.domElement.getBoundingClientRect();
        this.mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
        this.mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
        this.raycaster.setFromCamera(this.mouse, this.camera);
        const hits = this.raycaster.intersectObjects(this._bayMeshes, false);
        const idx = hits.length ? hits[0].object.userData.idx : -1;
        if (idx !== this._hoverIdx) {
            hov2d = idx;
            this.setHover(idx);
        }
        showTooltip(idx, e);
    }

    _onClick(e) {
        if (this._hoverIdx < 0) return;
        const i = this._hoverIdx;
        sel = sel === i ? -1 : i;
        this.setSelected(sel);
        document.querySelectorAll('.bay-item').forEach((el, j) => el.classList.toggle('active', j === sel));
        if (sel >= 0) document.querySelector(`.bay-item[data-idx="${sel}"]`)?.scrollIntoView({ block: 'nearest' });
        draw2d();
    }

    _onResize() {
        const w = this.container.clientWidth;
        const h = this.container.clientHeight;
        this.camera.aspect = w / Math.max(1, h);
        this.camera.updateProjectionMatrix();
        this.renderer.setSize(w, h);
    }

    _animate() {
        if (!this._running) return;
        this.controls.update();
        this.renderer.render(this.scene, this.camera);
        requestAnimationFrame(() => this._animate());
    }
}

let view3d = null;
function ensureThreeView() {
    if (view3d) { view3d._onResize(); return; }
    const el = document.getElementById('canvas3d');
    view3d = new ThreeView(el);
}

/* ── Mode toggle ───────────────────────────── */
const stage = document.getElementById('stage');
let page = 'scene';  // 'scene' | 'catalog'

function applyStageClasses() {
    stage.classList.remove('mode-2d', 'mode-3d', 'mode-catalog');
    if (page === 'catalog') {
        stage.classList.add('mode-catalog');
    } else {
        stage.classList.add(viewMode === '3d' ? 'mode-3d' : 'mode-2d');
    }
    const is3d = viewMode === '3d';
    const isScene = page === 'scene';
    // Hide 2D/3D toggle when in Catalog page (catalog cards are always 2D)
    document.getElementById('modeToggle').style.display = isScene ? '' : 'none';
    document.getElementById('float2d').style.display = isScene && !is3d ? '' : 'none';
    document.getElementById('float3d').style.display = isScene && is3d ? '' : 'none';
    document.getElementById('legend3d').style.display = isScene && is3d ? '' : 'none';
    document.querySelectorAll('.toggle[data-mode="3d"]').forEach(el =>
        el.style.display = isScene && is3d ? '' : 'none'
    );
    if (page === 'scene' && is3d) ensureThreeView();
    if (page === 'catalog') renderCatalog();
}

document.querySelectorAll('#modeToggle .vmode-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        const mode = btn.dataset.mode;
        if (mode === viewMode) return;
        viewMode = mode;
        document.querySelectorAll('#modeToggle .vmode-btn').forEach(b => b.classList.toggle('active', b === btn));
        applyStageClasses();
    });
});

document.querySelectorAll('#pageToggle .vmode-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        const p = btn.dataset.page;
        if (p === page) return;
        page = p;
        document.querySelectorAll('#pageToggle .vmode-btn').forEach(b => b.classList.toggle('active', b === btn));
        applyStageClasses();
    });
});

// Hide 3D-only controls in 2D mode initially
document.querySelectorAll('.toggle[data-mode="3d"]').forEach(el => el.style.display = 'none');

/* ── Catalog view ──────────────────────────── */
let _catalogBuilt = false;

function renderCatalog() {
    const grid = document.getElementById('catalogGrid');
    document.getElementById('catalogCount').textContent =
        `${BAY_TYPES.length} type${BAY_TYPES.length === 1 ? '' : 's'}`;
    if (_catalogBuilt) return;
    grid.innerHTML = '';
    BAY_TYPES.forEach((bt, idx) => {
        const card = document.createElement('div');
        card.className = 'catalog-card';
        card.setAttribute('tabindex', '0');
        const col = colorFor(bt.id);
        card.innerHTML = `
            <div class="catalog-preview" data-idx="${idx}">
                <div class="catalog-chip" style="background:${col}"></div>
                <div class="catalog-id">ID ${bt.id}</div>
                <div class="catalog-hint">Click for 3D</div>
            </div>
            <div class="catalog-body">
                <div class="catalog-title">Bay Type ${bt.id}</div>
                <div class="catalog-dims">${bt.w} × ${bt.d} × ${bt.h} mm</div>
                <div class="catalog-meta">
                    <div><b>Gap</b> ${bt.gap} mm</div>
                    <div><b>Loads</b> ${bt.ld}</div>
                    <div><b>Price</b> ${bt.pr}</div>
                    <div><b>€/load</b> ${(bt.pr / Math.max(1, bt.ld)).toFixed(1)}</div>
                </div>
            </div>
        `;
        const preview = card.querySelector('.catalog-preview');
        renderCatalogItem2D(preview, bt);
        card.addEventListener('click', () => openCatalogModal(bt));
        card.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                openCatalogModal(bt);
            }
        });
        grid.appendChild(card);
    });
    _catalogBuilt = true;
}

function renderCatalogItem2D(container, bt) {
    const c = document.createElement('canvas');
    container.appendChild(c);
    const draw = () => {
        const r = container.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) return;
        c.width = r.width * devicePixelRatio;
        c.height = r.height * devicePixelRatio;
        c.style.width = r.width + 'px';
        c.style.height = r.height + 'px';
        const cx = c.getContext('2d');
        cx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
        const w = r.width, h = r.height;
        cx.clearRect(0, 0, w, h);

        // Fit body + gap preserving the true aspect ratio
        const pad = 26;
        const totalW = bt.w;
        const totalH = bt.d + bt.gap;
        const s = Math.min((w - pad * 2) / totalW, (h - pad * 2) / totalH);
        const bw = bt.w * s, bd = bt.d * s, bg = bt.gap * s;
        const x0 = (w - bw) / 2;
        const y0 = (h - (bd + bg)) / 2;

        // Gap (north of body)
        cx.fillStyle = 'rgba(108, 156, 255, 0.12)';
        cx.fillRect(x0, y0, bw, bg);
        cx.setLineDash([4, 4]);
        cx.strokeStyle = 'rgba(108, 156, 255, 0.45)';
        cx.lineWidth = 1;
        cx.strokeRect(x0, y0, bw, bg);
        cx.setLineDash([]);

        // Body
        const col = colorFor(bt.id);
        const rgb = hexToRgb(col);
        const g = cx.createLinearGradient(x0, y0 + bg, x0, y0 + bg + bd);
        g.addColorStop(0, `rgba(${rgb.r},${rgb.g},${rgb.b},0.9)`);
        g.addColorStop(1, `rgba(${Math.floor(rgb.r * 0.7)},${Math.floor(rgb.g * 0.7)},${Math.floor(rgb.b * 0.7)},0.9)`);
        const r2 = Math.min(3, bw / 6, bd / 6);
        cx.beginPath();
        cx.moveTo(x0 + r2, y0 + bg);
        cx.lineTo(x0 + bw - r2, y0 + bg);
        cx.arcTo(x0 + bw, y0 + bg, x0 + bw, y0 + bg + r2, r2);
        cx.lineTo(x0 + bw, y0 + bg + bd - r2);
        cx.arcTo(x0 + bw, y0 + bg + bd, x0 + bw - r2, y0 + bg + bd, r2);
        cx.lineTo(x0 + r2, y0 + bg + bd);
        cx.arcTo(x0, y0 + bg + bd, x0, y0 + bg + bd - r2, r2);
        cx.lineTo(x0, y0 + bg + r2);
        cx.arcTo(x0, y0 + bg, x0 + r2, y0 + bg, r2);
        cx.closePath();
        cx.fillStyle = g; cx.fill();
        cx.strokeStyle = 'rgba(255,255,255,0.15)';
        cx.lineWidth = 1; cx.stroke();

        if (bg > 14) {
            cx.fillStyle = 'rgba(108, 156, 255, 0.85)';
            cx.font = '600 10px -apple-system, sans-serif';
            cx.textAlign = 'center';
            cx.textBaseline = 'middle';
            cx.fillText('gap', x0 + bw / 2, y0 + bg / 2);
        }
        cx.fillStyle = 'rgba(255,255,255,0.75)';
        cx.font = '500 10px ui-monospace, Menlo, monospace';
        cx.textAlign = 'center';
        cx.textBaseline = 'top';
        cx.fillText(`${bt.w} mm`, x0 + bw / 2, y0 + bg + bd + 4);
        cx.save();
        cx.translate(x0 - 6, y0 + bg + bd / 2);
        cx.rotate(-Math.PI / 2);
        cx.textBaseline = 'bottom';
        cx.fillText(`${bt.d} mm`, 0, 0);
        cx.restore();
    };
    draw();
    new ResizeObserver(draw).observe(container);
}

/* ── Catalog 3D modal ──────────────────────── */
let _modal3dState = null;

function openCatalogModal(bt) {
    const overlay = document.getElementById('catalogModal');
    const col = colorFor(bt.id);
    document.getElementById('modalChip').style.background = col;
    document.getElementById('modalTitle').textContent = `Bay Type ${bt.id}`;
    document.getElementById('modalInfo').innerHTML = `
        <div class="row"><div class="k">ID</div><div class="v">${bt.id}</div></div>
        <div class="row"><div class="k">Width</div><div class="v">${bt.w} mm</div></div>
        <div class="row"><div class="k">Depth</div><div class="v">${bt.d} mm</div></div>
        <div class="row"><div class="k">Height</div><div class="v">${bt.h} mm</div></div>
        <div class="row"><div class="k">Gap</div><div class="v">${bt.gap} mm</div></div>
        <div class="row"><div class="k">Loads</div><div class="v">${bt.ld}</div></div>
        <div class="row"><div class="k">Price</div><div class="v">${bt.pr}</div></div>
        <div class="row"><div class="k">€ / Load</div><div class="v">${(bt.pr / Math.max(1, bt.ld)).toFixed(2)}</div></div>
        <div class="row"><div class="k">Footprint</div><div class="v">${(bt.w * bt.d / 1e6).toFixed(2)} m²</div></div>
    `;
    overlay.classList.add('open');
    // Build 3D once the container has dimensions
    requestAnimationFrame(() => mountModal3D(bt));
}

function closeCatalogModal() {
    document.getElementById('catalogModal').classList.remove('open');
    if (_modal3dState) {
        try { _modal3dState.dispose(); } catch (e) { /* ignore */ }
        _modal3dState = null;
    }
}

function mountModal3D(bt) {
    if (_modal3dState) { try { _modal3dState.dispose(); } catch (e) {} _modal3dState = null; }
    const container = document.getElementById('modal3d');
    const r0 = container.getBoundingClientRect();

    const scene = new THREE.Scene();
    scene.background = null;
    const aspect = r0.width / Math.max(1, r0.height);
    const camera = new THREE.PerspectiveCamera(40, aspect, 1, 400000);
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    renderer.setSize(r0.width, r0.height);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    container.appendChild(renderer.domElement);
    renderer.domElement.style.width = '100%';
    renderer.domElement.style.height = '100%';
    renderer.domElement.style.display = 'block';

    // Lights
    scene.add(new THREE.AmbientLight(0xffffff, 0.55));
    scene.add(new THREE.HemisphereLight(0xbfd4ff, 0x20222a, 0.6));
    const dir = new THREE.DirectionalLight(0xffffff, 0.9);
    dir.position.set(bt.w, bt.h * 2 + 2000, bt.d);
    scene.add(dir);

    // Floor + grid
    const floorSize = Math.max(bt.w, bt.d) * 2.4;
    const floor = new THREE.Mesh(
        new THREE.CircleGeometry(floorSize / 2, 64),
        new THREE.MeshStandardMaterial({ color: 0x1a1e2a, roughness: 0.95 })
    );
    floor.rotation.x = -Math.PI / 2;
    scene.add(floor);
    const grid = new THREE.GridHelper(floorSize, 12, 0x2a2e38, 0x20242e);
    grid.position.y = 1;
    scene.add(grid);

    // Body
    const col = new THREE.Color(colorFor(bt.id));
    const bodyGeo = new THREE.BoxGeometry(bt.w, bt.h, bt.d);
    const bodyMat = new THREE.MeshStandardMaterial({
        color: col, roughness: 0.55, metalness: 0.15,
        emissive: col.clone().multiplyScalar(0.06),
    });
    const body = new THREE.Mesh(bodyGeo, bodyMat);
    body.position.set(0, bt.h / 2, 0);
    scene.add(body);

    const edges = new THREE.LineSegments(
        new THREE.EdgesGeometry(bodyGeo),
        new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.2 })
    );
    edges.position.copy(body.position);
    scene.add(edges);

    // Gap slab
    let gapGeo = null, gapEdgesGeo = null;
    if (bt.gap > 0) {
        gapGeo = new THREE.BoxGeometry(bt.w, Math.max(1, bt.h * 0.04), bt.gap);
        const gap = new THREE.Mesh(gapGeo, new THREE.MeshBasicMaterial({
            color: 0x6c9cff, transparent: true, opacity: 0.18,
        }));
        gap.position.set(0, 1, -bt.d / 2 - bt.gap / 2);
        scene.add(gap);
        gapEdgesGeo = new THREE.BoxGeometry(bt.w, 1, bt.gap);
        const gapEdges = new THREE.LineSegments(
            new THREE.EdgesGeometry(gapEdgesGeo),
            new THREE.LineBasicMaterial({
                color: 0x6c9cff, transparent: true, opacity: 0.6,
            })
        );
        gapEdges.position.set(0, 1, -bt.d / 2 - bt.gap / 2);
        scene.add(gapEdges);
    }

    // Camera + OrbitControls
    const d = Math.max(bt.w, bt.d, bt.h) * 2.4 + 1500;
    camera.position.set(d * 0.8, d * 0.7, d * 1.0);
    camera.lookAt(0, bt.h / 2, 0);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.target.set(0, bt.h / 2, 0);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.minDistance = Math.max(bt.w, bt.d, bt.h) * 0.6;
    controls.maxDistance = d * 4;
    controls.maxPolarAngle = Math.PI / 2 - 0.02;
    controls.update();

    let running = true;
    const animate = () => {
        if (!running) return;
        controls.update();
        renderer.render(scene, camera);
        requestAnimationFrame(animate);
    };
    requestAnimationFrame(animate);

    const ro = new ResizeObserver(() => {
        const r = container.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) return;
        camera.aspect = r.width / r.height;
        camera.updateProjectionMatrix();
        renderer.setSize(r.width, r.height);
    });
    ro.observe(container);

    _modal3dState = {
        dispose() {
            running = false;
            ro.disconnect();
            controls.dispose();
            renderer.dispose();
            bodyGeo.dispose();
            bodyMat.dispose();
            if (gapGeo) gapGeo.dispose();
            if (gapEdgesGeo) gapEdgesGeo.dispose();
            if (renderer.domElement.parentElement === container) {
                container.removeChild(renderer.domElement);
            }
        }
    };
}

document.getElementById('modalClose').addEventListener('click', closeCatalogModal);
document.getElementById('catalogModal').addEventListener('click', (e) => {
    if (e.target.id === 'catalogModal') closeCatalogModal();
});
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && document.getElementById('catalogModal').classList.contains('open')) {
        closeCatalogModal();
    }
});

// 3D view buttons
document.getElementById('view3dTop').addEventListener('click', () => view3d?.setTopView());
document.getElementById('view3dIso').addEventListener('click', () => view3d?.setIso());
document.getElementById('view3dFront').addEventListener('click', () => view3d?.setFrontView());

// Initial render
renderStats();
renderBayList();
resize2d();
window.addEventListener('resize', () => {
    resize2d();
    if (view3d) view3d._onResize();
});
</script>

</body>
</html>
"""


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

def _parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Render a 2D/3D HTML visualization of a warehouse solution, "
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
            print("ERROR: --labels must contain exactly two comma-separated values",
                  file=sys.stderr)
            sys.exit(2)
        labels = parts

    output_html = args.output or args.legacy_output
    if output_html is None:
        output_html = 'visualization.html' if not args.solution_b else 'compare.html'

    generate_html(args.case_dir, solution_files, labels, output_html)
