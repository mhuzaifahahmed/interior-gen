"""Standalone, local test for the AutoCAD (.dxf) export feature ONLY - runs
the free room-layout + blueprint steps and writes the PNG/DXF files to disk
for manual inspection. Deliberately does NOT call run_house_pipeline() /
generate_house_render() - it never touches OpenAI, so it costs nothing and
never triggers a paid render.

Usage (from the repo root, with the project's venv):
    .venv/Scripts/python.exe scripts/test_blueprint_dxf.py

Edit LENGTH / WIDTH / UNIT / REQUIREMENTS below to try different plots.
Output lands in scripts/output/ (gitignored-safe scratch dir - delete anytime).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.pipeline.blueprint_dxf import render_floor_blueprint_dxf
from app.pipeline.blueprint_svg import render_floor_blueprint
from app.pipeline.floor_layout import layout_floor
from app.providers.gemini import GeminiProvider

LENGTH = 40
WIDTH = 60
UNIT = "ft"
FLOOR_COUNT = 2
REQUIREMENTS = "2 floors, 3 bedrooms, 2 bathrooms. Extras: garage, modern style"

if __name__ == "__main__":
    dimensions = {"length": LENGTH, "width": WIDTH, "unit": UNIT}

    print(f"Requesting room layout from Gemini for a {LENGTH}x{WIDTH}{UNIT} plot...")
    provider = GeminiProvider()
    room_layout = provider.generate_room_layout(dimensions, REQUIREMENTS, floor_count=FLOOR_COUNT)
    total_floors = len(room_layout["floors"])
    print(f"Got {total_floors} floor(s) back.")

    out_dir = Path(__file__).resolve().parent / "output"
    out_dir.mkdir(exist_ok=True)

    for floor in room_layout["floors"]:
        n = floor["floor_number"]
        rects = layout_floor(floor["rooms"], dimensions)
        print(f"\nFloor {n} rooms:")
        for r in rects:
            print(f"  {r['name']}: {r['w']:.1f}x{r['h']:.1f}{UNIT} at ({r['x']:.1f}, {r['y']:.1f})")

        png_bytes = render_floor_blueprint(n, rects, dimensions, total_floors)
        (out_dir / f"floor{n}.png").write_bytes(png_bytes)

        dxf_bytes = render_floor_blueprint_dxf(n, rects, dimensions)
        (out_dir / f"floor{n}.dxf").write_bytes(dxf_bytes)

    print(f"\nDone - wrote {total_floors} PNG(s) and {total_floors} DXF(s) to {out_dir}")
    print("Open the .dxf files in AutoCAD (or any DXF viewer) to check them.")
    print("No OpenAI call was made - this only used the free Gemini text quota.")
