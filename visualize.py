#!/usr/bin/env python3
"""
Generate an HTML visualization of a warehouse solution.
Usage: python3 visualize.py <case_dir> <solution_file> [output_html]
"""

import sys
import os
import json
import math

from solver import (
    parse_warehouse, parse_obstacles, parse_ceiling, parse_bay_types,
    PlacedBay, compute_score,
)


def generate_html(case_dir: str, solution_file: str, output_html: str):
    warehouse = parse_warehouse(os.path.join(case_dir, 'warehouse.csv'))
    obstacles = parse_obstacles(os.path.join(case_dir, 'obstacles.csv'))
    ceiling = parse_ceiling(os.path.join(case_dir, 'ceiling.csv'))
    bay_types = parse_bay_types(os.path.join(case_dir, 'types_of_bays.csv'))

    bay_type_map = {bt.id: bt for bt in bay_types}
    warehouse_area = warehouse.area

    # Parse solution
    placed = []
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
            bt = bay_type_map[bay_id]
            placed.append(PlacedBay(bay_type=bt, x=x, y=y, rotation=rot))

    # Compute stats
    score = compute_score(placed, warehouse_area)
    total_area = sum(pb.bay_type.area for pb in placed)
    coverage = total_area / warehouse_area
    total_price = sum(pb.bay_type.price for pb in placed)
    total_loads = sum(pb.bay_type.n_loads for pb in placed)

    # Get bounds for viewport
    wx, wy = warehouse.exterior.xy
    all_x = list(wx)
    all_y = list(wy)
    min_x = min(all_x)
    max_x = max(all_x)
    min_y = min(all_y)
    max_y = max(all_y)

    # Warehouse polygon coords
    wh_coords = list(zip([int(x) for x in wx], [int(y) for y in wy]))

    # Obstacles data
    obs_data = []
    for obs in obstacles:
        bminx, bminy, bmaxx, bmaxy = obs.bounds
        obs_data.append({
            'x': bminx, 'y': bminy,
            'w': bmaxx - bminx, 'h': bmaxy - bminy
        })

    # Ceiling data
    ceil_data = [{'x': bp[0], 'h': bp[1]} for bp in ceiling.breakpoints]

    # Placed bays data
    bays_data = []
    for i, pb in enumerate(placed):
        w, d = pb.get_body_dims()
        body_gap = pb.get_body_with_gap_polygon()
        gminx, gminy, gmaxx, gmaxy = body_gap.bounds

        bays_data.append({
            'idx': i,
            'type_id': pb.bay_type.id,
            'x': pb.x, 'y': pb.y,
            'w': w, 'd': d,
            'rot': pb.rotation,
            'height': pb.bay_type.height,
            'gap': pb.bay_type.gap,
            'price': pb.bay_type.price,
            'loads': pb.bay_type.n_loads,
            'gap_x': gminx, 'gap_y': gminy,
            'gap_w': gmaxx - gminx, 'gap_h': gmaxy - gminy,
        })

    case_name = os.path.basename(case_dir)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Warehouse Visualizer — {case_name}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: #1a1a2e; color: #eee; }}
.header {{
    background: #16213e; padding: 12px 24px; display: flex;
    justify-content: space-between; align-items: center;
    border-bottom: 2px solid #0f3460;
}}
.header h1 {{ font-size: 18px; color: #e94560; }}
.stats {{ display: flex; gap: 24px; font-size: 13px; }}
.stat {{ text-align: center; }}
.stat .val {{ font-size: 20px; font-weight: bold; color: #0f3460; }}
.stat .val.good {{ color: #4ecca3; }}
.stat .val.warn {{ color: #e94560; }}
.stat .label {{ color: #999; font-size: 11px; }}
.controls {{
    background: #16213e; padding: 8px 24px; display: flex;
    gap: 16px; align-items: center; font-size: 13px;
    border-bottom: 1px solid #0f3460;
}}
.controls label {{ color: #999; }}
.controls input[type=checkbox] {{ margin-right: 4px; }}
canvas {{ display: block; cursor: grab; }}
canvas:active {{ cursor: grabbing; }}
.tooltip {{
    position: fixed; background: #16213e; border: 1px solid #0f3460;
    padding: 8px 12px; border-radius: 6px; font-size: 12px;
    pointer-events: none; display: none; z-index: 100;
    box-shadow: 0 4px 12px rgba(0,0,0,0.5);
}}
</style>
</head>
<body>
<div class="header">
    <h1>🏭 {case_name} — Warehouse Optimizer</h1>
    <div class="stats">
        <div class="stat">
            <div class="val good">{len(placed)}</div>
            <div class="label">Bays</div>
        </div>
        <div class="stat">
            <div class="val {'good' if coverage > 0.6 else 'warn'}">{coverage:.1%}</div>
            <div class="label">Coverage</div>
        </div>
        <div class="stat">
            <div class="val">{score:.1f}</div>
            <div class="label">Score Q</div>
        </div>
        <div class="stat">
            <div class="val">{total_price}</div>
            <div class="label">Total Price</div>
        </div>
        <div class="stat">
            <div class="val">{total_loads}</div>
            <div class="label">Total Loads</div>
        </div>
    </div>
</div>
<div class="controls">
    <label><input type="checkbox" id="showGaps" checked> Show Gaps</label>
    <label><input type="checkbox" id="showObstacles" checked> Show Obstacles</label>
    <label><input type="checkbox" id="showLabels" checked> Show Labels</label>
    <label><input type="checkbox" id="showGrid"> Show Grid</label>
    <span style="color:#666">| Scroll to zoom, drag to pan</span>
</div>
<canvas id="canvas"></canvas>
<div class="tooltip" id="tooltip"></div>

<script>
const WAREHOUSE = {json.dumps(wh_coords)};
const OBSTACLES = {json.dumps(obs_data)};
const CEILING = {json.dumps(ceil_data)};
const BAYS = {json.dumps(bays_data)};
const BOUNDS = {{ minX: {min_x}, minY: {min_y}, maxX: {max_x}, maxY: {max_y} }};

const canvas = document.getElementById('canvas');
const ctx = canvas.getContext('2d');
const tooltip = document.getElementById('tooltip');

// Colors for bay types
const BAY_COLORS = [
    '#e94560','#0f3460','#4ecca3','#f9a825','#7c4dff','#00bcd4',
    '#ff7043','#66bb6a','#ab47bc','#26c6da','#ef5350','#5c6bc0',
    '#8d6e63','#78909c','#d4e157','#29b6f6'
];

let scale = 1;
let offsetX = 0, offsetY = 0;
let dragging = false;
let dragStartX, dragStartY;
let hoveredBay = -1;

function resize() {{
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight - canvas.getBoundingClientRect().top;
    fitToView();
    draw();
}}

function fitToView() {{
    const pad = 60;
    const spanX = BOUNDS.maxX - BOUNDS.minX;
    const spanY = BOUNDS.maxY - BOUNDS.minY;
    const scaleX = (canvas.width - pad * 2) / spanX;
    const scaleY = (canvas.height - pad * 2) / spanY;
    scale = Math.min(scaleX, scaleY);
    offsetX = (canvas.width - spanX * scale) / 2 - BOUNDS.minX * scale;
    offsetY = (canvas.height - spanY * scale) / 2 - BOUNDS.minY * scale;
}}

function toScreen(x, y) {{
    // Flip Y so +Y goes up
    return [x * scale + offsetX, canvas.height - (y * scale + offsetY)];
}}

function toWorld(sx, sy) {{
    return [(sx - offsetX) / scale, (canvas.height - sy - offsetY) / scale];
}}

function draw() {{
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    const showGaps = document.getElementById('showGaps').checked;
    const showObs = document.getElementById('showObstacles').checked;
    const showLabels = document.getElementById('showLabels').checked;
    const showGrid = document.getElementById('showGrid').checked;

    // Grid
    if (showGrid) {{
        ctx.strokeStyle = '#ffffff10';
        ctx.lineWidth = 0.5;
        const spanX = BOUNDS.maxX - BOUNDS.minX;
        const spanY = BOUNDS.maxY - BOUNDS.minY;
        const gridStep = Math.pow(10, Math.floor(Math.log10(Math.max(spanX, spanY))) - 1);
        for (let gx = Math.floor(BOUNDS.minX / gridStep) * gridStep; gx <= BOUNDS.maxX; gx += gridStep) {{
            const [sx] = toScreen(gx, 0);
            ctx.beginPath(); ctx.moveTo(sx, 0); ctx.lineTo(sx, canvas.height); ctx.stroke();
        }}
        for (let gy = Math.floor(BOUNDS.minY / gridStep) * gridStep; gy <= BOUNDS.maxY; gy += gridStep) {{
            const [, sy] = toScreen(0, gy);
            ctx.beginPath(); ctx.moveTo(0, sy); ctx.lineTo(canvas.width, sy); ctx.stroke();
        }}
    }}

    // Warehouse fill
    ctx.beginPath();
    for (let i = 0; i < WAREHOUSE.length; i++) {{
        const [sx, sy] = toScreen(WAREHOUSE[i][0], WAREHOUSE[i][1]);
        if (i === 0) ctx.moveTo(sx, sy); else ctx.lineTo(sx, sy);
    }}
    ctx.closePath();
    ctx.fillStyle = '#1a1a2e';
    ctx.fill();
    ctx.strokeStyle = '#eee';
    ctx.lineWidth = 2;
    ctx.stroke();

    // Obstacles
    if (showObs) {{
        for (const obs of OBSTACLES) {{
            const [sx, sy] = toScreen(obs.x, obs.y + obs.h);
            const w = obs.w * scale;
            const h = obs.h * scale;
            ctx.fillStyle = '#ff000030';
            ctx.fillRect(sx, sy, w, h);
            ctx.strokeStyle = '#ff0000';
            ctx.lineWidth = 1;
            ctx.strokeRect(sx, sy, w, h);

            // Hatch pattern
            ctx.save();
            ctx.beginPath();
            ctx.rect(sx, sy, w, h);
            ctx.clip();
            ctx.strokeStyle = '#ff000050';
            ctx.lineWidth = 1;
            for (let d = -Math.max(w,h); d < Math.max(w,h) * 2; d += 8) {{
                ctx.beginPath();
                ctx.moveTo(sx + d, sy);
                ctx.lineTo(sx + d + Math.max(w,h), sy + Math.max(w,h));
                ctx.stroke();
            }}
            ctx.restore();
        }}
    }}

    // Bays — gaps first (behind bodies)
    if (showGaps) {{
        for (const bay of BAYS) {{
            const [gsx, gsy] = toScreen(bay.gap_x, bay.gap_y + bay.gap_h);
            const gw = bay.gap_w * scale;
            const gh = bay.gap_h * scale;
            ctx.fillStyle = '#ffffff08';
            ctx.fillRect(gsx, gsy, gw, gh);
            ctx.strokeStyle = '#ffffff20';
            ctx.lineWidth = 0.5;
            ctx.setLineDash([4, 4]);
            ctx.strokeRect(gsx, gsy, gw, gh);
            ctx.setLineDash([]);
        }}
    }}

    // Bays — bodies
    for (let i = 0; i < BAYS.length; i++) {{
        const bay = BAYS[i];
        const [sx, sy] = toScreen(bay.x, bay.y + bay.d);
        const w = bay.w * scale;
        const h = bay.d * scale;
        const color = BAY_COLORS[bay.type_id % BAY_COLORS.length];

        const isHovered = (i === hoveredBay);

        ctx.fillStyle = isHovered ? color + 'cc' : color + '80';
        ctx.fillRect(sx, sy, w, h);
        ctx.strokeStyle = isHovered ? '#fff' : color;
        ctx.lineWidth = isHovered ? 2 : 1;
        ctx.strokeRect(sx, sy, w, h);

        // Label
        if (showLabels && w > 20 && h > 14) {{
            ctx.fillStyle = '#fff';
            ctx.font = `${{Math.min(12, Math.max(8, h * 0.4))}}px monospace`;
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(`${{bay.type_id}}`, sx + w/2, sy + h/2);
        }}
    }}

    // Warehouse outline (on top)
    ctx.beginPath();
    for (let i = 0; i < WAREHOUSE.length; i++) {{
        const [sx, sy] = toScreen(WAREHOUSE[i][0], WAREHOUSE[i][1]);
        if (i === 0) ctx.moveTo(sx, sy); else ctx.lineTo(sx, sy);
    }}
    ctx.closePath();
    ctx.strokeStyle = '#eee';
    ctx.lineWidth = 2;
    ctx.stroke();
}}

// Mouse interaction
canvas.addEventListener('wheel', (e) => {{
    e.preventDefault();
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;

    const zoomFactor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
    const [wx, wy] = toWorld(mx, my);

    scale *= zoomFactor;
    offsetX = (mx - wx * scale);
    offsetY = -(my - canvas.height) - wy * scale;

    draw();
}});

canvas.addEventListener('mousedown', (e) => {{
    dragging = true;
    dragStartX = e.clientX;
    dragStartY = e.clientY;
}});

canvas.addEventListener('mousemove', (e) => {{
    if (dragging) {{
        offsetX += e.clientX - dragStartX;
        offsetY -= e.clientY - dragStartY;
        dragStartX = e.clientX;
        dragStartY = e.clientY;
        draw();
        return;
    }}

    // Hover detection
    const rect = canvas.getBoundingClientRect();
    const [wx, wy] = toWorld(e.clientX - rect.left, e.clientY - rect.top);

    let found = -1;
    for (let i = BAYS.length - 1; i >= 0; i--) {{
        const b = BAYS[i];
        if (wx >= b.x && wx <= b.x + b.w && wy >= b.y && wy <= b.y + b.d) {{
            found = i;
            break;
        }}
    }}

    if (found !== hoveredBay) {{
        hoveredBay = found;
        draw();
    }}

    if (found >= 0) {{
        const b = BAYS[found];
        tooltip.style.display = 'block';
        tooltip.style.left = (e.clientX + 12) + 'px';
        tooltip.style.top = (e.clientY + 12) + 'px';
        tooltip.innerHTML = `
            <b>Bay #${{found}}</b> (type ${{b.type_id}})<br>
            Pos: (${{b.x}}, ${{b.y}}) rot=${{b.rot}}°<br>
            Size: ${{b.w}} × ${{b.d}} (h=${{b.height}})<br>
            Gap: ${{b.gap}}mm<br>
            Price: ${{b.price}} | Loads: ${{b.loads}}<br>
            P/L: ${{(b.price/b.loads).toFixed(1)}}
        `;
    }} else {{
        tooltip.style.display = 'none';
    }}
}});

canvas.addEventListener('mouseup', () => {{ dragging = false; }});
canvas.addEventListener('mouseleave', () => {{
    dragging = false;
    tooltip.style.display = 'none';
}});

// Checkbox listeners
for (const id of ['showGaps','showObstacles','showLabels','showGrid']) {{
    document.getElementById(id).addEventListener('change', draw);
}}

window.addEventListener('resize', resize);
resize();
</script>
</body>
</html>"""

    with open(output_html, 'w') as f:
        f.write(html)

    print(f"Visualization written to {output_html}")


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage: python3 visualize.py <case_dir> <solution_file> [output.html]")
        sys.exit(1)

    case_dir = sys.argv[1]
    solution_file = sys.argv[2]
    output_html = sys.argv[3] if len(sys.argv) > 3 else 'visualization.html'

    generate_html(case_dir, solution_file, output_html)
