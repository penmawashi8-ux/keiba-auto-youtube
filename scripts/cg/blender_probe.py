"""Blender が GitHub Actions で描画できるか・1フレームの時間を測る試験（blender -b -P で実行）。"""
import sys
import time

import bpy

out = sys.argv[sys.argv.index("--") + 1]
bpy.ops.wm.read_factory_settings(use_empty=True)
sc = bpy.context.scene
bpy.ops.mesh.primitive_plane_add(size=200)
g = bpy.context.object
mg = bpy.data.materials.new("turf")
mg.diffuse_color = (0.15, 0.5, 0.15, 1)
g.data.materials.append(mg)
for i in range(18):                      # 馬の代わりの箱
    bpy.ops.mesh.primitive_cube_add(size=1, location=(i * 1.5 - 13, 0, 1))
    o = bpy.context.object
    o.scale = (1.2, 0.4, 0.8)
bpy.ops.object.camera_add(location=(0, -25, 10), rotation=(1.2, 0, 0))
sc.camera = bpy.context.object
bpy.ops.object.light_add(type="SUN", location=(0, 0, 20))
w = bpy.data.worlds.new("w")
w.color = (0.5, 0.7, 1.0)
sc.world = w
sc.render.resolution_x, sc.render.resolution_y = 1920, 1080
for eng in ("BLENDER_WORKBENCH", "BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "CYCLES"):
    try:
        sc.render.engine = eng
    except TypeError:
        print("engine なし", eng)
        continue
    if eng == "CYCLES":
        sc.cycles.samples = 16
    sc.render.filepath = f"{out}/probe_{eng}.png"
    t = time.time()
    try:
        bpy.ops.render.render(write_still=True)
        print(f"PROBE {eng}: {time.time() - t:.2f}s")
    except Exception as e:
        print(f"PROBE {eng}: 失敗 {e}")
print("version", bpy.app.version_string)
