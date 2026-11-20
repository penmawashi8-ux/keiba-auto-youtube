"""3D CG のレース映像を Blender で描く。

使い方: blender -b -P scripts/cg/race3d.py -- spec.json 出力ディレクトリ 最初のフレーム 最後のフレーム

spec.json（scripts/sim_cg.py が書く）:
  course: lap, straight, right, track_w, dist, start_s, name
  horses: [{num, waku, name, coat, silk:[r,g,b], cap:[r,g,b]}]
  horse_model: 馬のモデル（.blend / .glb）。無ければ簡易の馬で描く
  frames: [{shot, tau, s:[各馬のコース上の距離], lat:[内ラチからの距離], cam?:{...}}]
    s はホームストレッチの始まり（4コーナー出口）からの距離[m]、1周 = lap
  fps, res:[w,h], samples

コースは「2本の直線＋2つの半円」で近似する（2D の sim_render と同じ形）。単位は m。
"""
import bpy
import bmesh
import json
import math
import random
import sys
from mathutils import Vector, Euler

argv = sys.argv[sys.argv.index("--") + 1:]
SPEC = json.load(open(argv[0], encoding="utf-8"))
OUT = argv[1]
if "," in argv[2]:                       # 「12,340,800」のように個別のフレームを指定（確認用）
    PICK = [int(x) for x in argv[2].split(",")]
    F0, F1 = min(PICK), max(PICK)
else:
    F0, F1 = int(argv[2]), int(argv[3])
    PICK = list(range(F0, F1 + 1))

C = SPEC["course"]
LAP, ST, RIGHT, TW = C["lap"], C["straight"], C["right"], C.get("track_w", 25.0)
R = (LAP - 2 * ST) / (2 * math.pi)
FINISH_S = ST                     # ゴールは直線の終わり
rnd = random.Random(7)


# ---------------------------------------------------------------------------
# コース上の座標
# ---------------------------------------------------------------------------
def _left(s, off):
    s %= LAP
    rr = R + off
    if s < ST:                                         # ホームストレッチ（-x → +x、y = -rr）
        return Vector((-ST / 2 + s, -rr, 0.0))
    s -= ST
    if s < math.pi * R:                                # 1〜2コーナー
        th = -math.pi / 2 + s / R
        return Vector((ST / 2 + rr * math.cos(th), rr * math.sin(th), 0.0))
    s -= math.pi * R
    if s < ST:                                         # バックストレッチ
        return Vector((ST / 2 - s, rr, 0.0))
    s -= ST
    th = math.pi / 2 + s / R                           # 3〜4コーナー
    return Vector((-ST / 2 + rr * math.cos(th), rr * math.sin(th), 0.0))


def P(s, off=0.0, z=0.0):
    """コース上の点。off は内ラチから外へ[m]。右回りは x を反転"""
    v = _left(s, off)
    if RIGHT:
        v.x = -v.x
    v.z = z
    return v


def heading(s, off=0.0):
    a, b = P(s - 0.5, off), P(s + 0.5, off)
    return math.atan2(b.y - a.y, b.x - a.x)


# ---------------------------------------------------------------------------
# マテリアル
# ---------------------------------------------------------------------------
def mat(name, color, rough=0.8, emit=0.0, spec=0.3):
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*color, 1)
    b.inputs["Roughness"].default_value = rough
    b.inputs["Specular IOR Level"].default_value = spec
    if emit:
        b.inputs["Emission Color"].default_value = (*color, 1)
        b.inputs["Emission Strength"].default_value = emit
    return m


def turf_mat():
    """芝：刈り込みの縞（10m幅）＋ムラ"""
    m = bpy.data.materials.new("Turf")
    m.use_nodes = True
    nt = m.node_tree
    b = nt.nodes["Principled BSDF"]
    b.inputs["Roughness"].default_value = 0.9
    b.inputs["Specular IOR Level"].default_value = 0.15
    tc = nt.nodes.new("ShaderNodeTexCoord")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(tc.outputs["UV"], sep.inputs[0])
    stripe = nt.nodes.new("ShaderNodeMath")
    stripe.operation = "PINGPONG"
    stripe.inputs[1].default_value = 1.0
    div = nt.nodes.new("ShaderNodeMath")
    div.operation = "DIVIDE"
    div.inputs[1].default_value = 9.0
    nt.links.new(sep.outputs[0], div.inputs[0])
    nt.links.new(div.outputs[0], stripe.inputs[0])
    rnd_ = nt.nodes.new("ShaderNodeMath")
    rnd_.operation = "ROUND"
    nt.links.new(stripe.outputs[0], rnd_.inputs[0])
    noise = nt.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 0.08
    noise.inputs["Detail"].default_value = 6
    tc2 = nt.nodes.new("ShaderNodeTexCoord")
    nt.links.new(tc2.outputs["Object"], noise.inputs["Vector"])
    mix = nt.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.inputs[6].default_value = (0.045, 0.16, 0.025, 1)
    mix.inputs[7].default_value = (0.07, 0.22, 0.035, 1)
    nt.links.new(rnd_.outputs[0], mix.inputs[0])
    mix2 = nt.nodes.new("ShaderNodeMix")
    mix2.data_type = "RGBA"
    mix2.blend_type = "MULTIPLY"
    mix2.inputs[0].default_value = 0.35
    nt.links.new(mix.outputs[2], mix2.inputs[6])
    nt.links.new(noise.outputs["Color"], mix2.inputs[7])
    nt.links.new(mix2.outputs[2], b.inputs["Base Color"])
    return m


def crowd_mat():
    """スタンドの観客：色の細かいムラ"""
    m = bpy.data.materials.new("Crowd")
    m.use_nodes = True
    nt = m.node_tree
    b = nt.nodes["Principled BSDF"]
    b.inputs["Roughness"].default_value = 1.0
    v = nt.nodes.new("ShaderNodeTexVoronoi")
    v.inputs["Scale"].default_value = 1.9            # 1人 ≒ 0.5m
    geo = nt.nodes.new("ShaderNodeNewGeometry")       # 箱を拡大して置くので、ワールド座標で模様を作る
    nt.links.new(geo.outputs["Position"], v.inputs["Vector"])
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    els = ramp.color_ramp.elements
    els[0].color = (0.05, 0.05, 0.06, 1)
    els[1].position = 0.9
    els[1].color = (0.75, 0.72, 0.68, 1)
    for p, c in [(0.2, (0.35, 0.08, 0.06, 1)), (0.4, (0.12, 0.15, 0.3, 1)), (0.6, (0.5, 0.45, 0.4, 1)),
                 (0.75, (0.2, 0.2, 0.22, 1))]:
        e = els.new(p)
        e.color = c
    nt.links.new(v.outputs["Color"], ramp.inputs[0])
    nt.links.new(ramp.outputs[0], b.inputs["Base Color"])
    return m


# ---------------------------------------------------------------------------
# メッシュ作り
# ---------------------------------------------------------------------------
def link(obj):
    bpy.context.scene.collection.objects.link(obj)
    return obj


def ribbon(name, off0, off1, z0, z1, material, step=2.0, s_from=0.0, s_to=None, uv_scale=1.0):
    """コースに沿った帯（off0〜off1、高さ z0〜z1。z0==z1 なら平面、off0==off1 なら壁）"""
    s_to = LAP if s_to is None else s_to
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    uv = bm.loops.layers.uv.new()
    n = max(int((s_to - s_from) / step), 2)
    prev = None
    for k in range(n + 1):
        s = s_from + (s_to - s_from) * k / n
        a = bm.verts.new(P(s, off0, z0))
        b = bm.verts.new(P(s, off1, z1))
        if prev:
            f = bm.faces.new((prev[0], prev[1], b, a))
            for l, (u, vv) in zip(f.loops, [(prev[2], 0), (prev[2], 1), (s, 1), (s, 0)]):
                l[uv].uv = (u * uv_scale, vv * abs(off1 - off0 + z1 - z0) * uv_scale)
        prev = (a, b, s)
    bm.to_mesh(me)
    bm.free()
    ob = link(bpy.data.objects.new(name, me))
    ob.data.materials.append(material)
    return ob


def disk(name, cx, cy, rx, ry, z, material, seg=96):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    vs = [bm.verts.new((cx + rx * math.cos(2 * math.pi * i / seg), cy + ry * math.sin(2 * math.pi * i / seg), z))
          for i in range(seg)]
    bm.faces.new(vs)
    bm.to_mesh(me)
    bm.free()
    ob = link(bpy.data.objects.new(name, me))
    ob.data.materials.append(material)
    return ob


def box(name, loc, size, material, rot_z=0.0):
    bpy.ops.mesh.primitive_cube_add(location=loc)
    ob = bpy.context.active_object
    ob.name = name
    ob.scale = (size[0] / 2, size[1] / 2, size[2] / 2)
    ob.rotation_euler = (0, 0, rot_z)
    ob.data.materials.append(material)
    return ob


def posts(name, off, every, h, w, material, s_from=0.0, s_to=None):
    """ラチの支柱（1つのメッシュにまとめる）"""
    s_to = LAP if s_to is None else s_to
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    s = s_from
    while s < s_to:
        c = P(s, off)
        hd = heading(s, off)
        ca, sa = math.cos(hd), math.sin(hd)
        corners = []
        for dx, dy in [(-w, -w), (w, -w), (w, w), (-w, w)]:
            corners.append((c.x + dx * ca - dy * sa, c.y + dx * sa + dy * ca))
        lo = [bm.verts.new((x, y, 0)) for x, y in corners]
        hi = [bm.verts.new((x, y, h)) for x, y in corners]
        for i in range(4):
            j = (i + 1) % 4
            bm.faces.new((lo[i], lo[j], hi[j], hi[i]))
        bm.faces.new(hi)
        s += every
    bm.to_mesh(me)
    bm.free()
    ob = link(bpy.data.objects.new(name, me))
    ob.data.materials.append(material)
    return ob


# ---------------------------------------------------------------------------
# シーン
# ---------------------------------------------------------------------------
def build_world():
    sc = bpy.context.scene
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    sc.render.engine = "BLENDER_EEVEE"
    ee = sc.eevee
    ee.taa_render_samples = SPEC.get("samples", 24)
    ee.use_gtao = True
    ee.gtao_distance = 2.0
    ee.use_soft_shadows = True
    ee.shadow_cascade_size = "4096"
    ee.shadow_cube_size = "1024"
    ee.use_ssr = False
    sc.render.resolution_x, sc.render.resolution_y = SPEC.get("res", [1280, 720])
    sc.render.fps = SPEC.get("fps", 30)
    sc.view_settings.view_transform = "Filmic"
    sc.view_settings.look = "Medium High Contrast"
    sc.view_settings.exposure = 0.0
    sc.render.image_settings.file_format = "JPEG"
    sc.render.image_settings.quality = 92

    w = bpy.data.worlds.new("Sky")
    sc.world = w
    w.use_nodes = True
    nt = w.node_tree
    bg = nt.nodes["Background"]
    sky = nt.nodes.new("ShaderNodeTexSky")
    sky.sky_type = "NISHITA"
    sky.sun_elevation = math.radians(24)
    sky.sun_rotation = math.radians(215)
    sky.altitude = 50
    sky.air_density = 1.5
    sky.dust_density = 0.5
    sky.ozone_density = 1.5
    sky.sun_disc = False
    nt.links.new(sky.outputs[0], bg.inputs[0])
    bg.inputs[1].default_value = 0.4

    sun = bpy.data.lights.new("Sun", "SUN")
    sun.energy = 4.5
    sun.color = (1.0, 0.93, 0.82)
    sun.angle = math.radians(1.5)
    so = link(bpy.data.objects.new("Sun", sun))
    so.rotation_euler = Euler((math.radians(90 - 24), 0, math.radians(215 + 90)))


def build_course():
    turf = turf_mat()
    dirt = mat("Dirt", (0.25, 0.16, 0.09), 1.0, spec=0.1)
    infield = mat("Infield", (0.05, 0.14, 0.03), 1.0, spec=0.1)
    outer = mat("OuterGrass", (0.06, 0.13, 0.04), 1.0, spec=0.1)
    white = mat("Rail", (0.92, 0.92, 0.9), 0.4)
    water = mat("Pond", (0.03, 0.12, 0.2), 0.35, spec=0.3)
    concrete = mat("Concrete", (0.62, 0.62, 0.6), 0.8)
    roof = mat("Roof", (0.28, 0.3, 0.33), 0.5)
    glass = mat("Glass", (0.15, 0.2, 0.25), 0.1, spec=0.8)

    ribbon("Turf", 0.0, TW, 0.0, 0.0, turf, uv_scale=1.0)
    ribbon("Dirt", -26.0, -3.0, 0.0, 0.0, dirt)
    ribbon("TurfEdgeIn", -3.0, 0.0, 0.0, 0.0, infield)
    # 内馬場・外側の地面
    disk("InfieldGround", 0, 0, ST / 2 + R + 60, R + 30, -0.03, infield)
    g = disk("Ground", 0, 0, 6000, 6000, -0.06, outer)
    # 内馬場の池（京都名物）
    disk("Pond", (-1 if RIGHT else 1) * 60, 10, 120, R - 75, 0.01, water)
    # ラチ（内・外）。上の横木＋支柱
    for nm, off in [("InnerRail", -0.6), ("OuterRail", TW + 0.6)]:
        ribbon(nm + "Top", off, off, 0.92, 1.06, white, step=1.5)
        ribbon(nm + "Top2", off - 0.08, off + 0.08, 1.06, 1.06, white, step=1.5)
        posts(nm + "Posts", off, 3.0, 1.0, 0.05, white)
    # ゴール板・ハロン棒
    goal = mat("GoalRed", (0.75, 0.05, 0.05), 0.5)
    gp = P(FINISH_S, -1.6)
    box("GoalPole", (gp.x, gp.y, 4.0), (0.25, 0.25, 8.0), white)
    bpy.ops.mesh.primitive_cylinder_add(radius=1.4, depth=0.12, location=(gp.x, gp.y, 7.0),
                                        rotation=(math.pi / 2, 0, heading(FINISH_S)))
    bpy.context.active_object.data.materials.append(goal)
    ribbon("GoalLine", 0, TW, 0.012, 0.012, white, s_from=FINISH_S - 0.15, s_to=FINISH_S + 0.15, step=0.3)
    red = mat("PoleRed", (0.7, 0.06, 0.05), 0.5)
    for k in range(1, int(C["dist"] / 200) + 2):
        s = FINISH_S - 200 * k
        p = P(s, -1.4)
        box(f"Furlong{k}", (p.x, p.y, 1.6), (0.18, 0.18, 3.2), red if k % 2 else white)
    # スタンド（ホームストレッチの外側）
    sx = (-1 if RIGHT else 1)
    y0 = -(R + TW + 18)
    xs0, xs1 = -ST / 2 - 40, ST / 2 + 60
    cx = sx * (xs0 + xs1) / 2
    width = xs1 - xs0
    crowd = crowd_mat()
    seat = mat("Seats", (0.16, 0.2, 0.26), 0.7)
    n_tier, dep, rise = 16, 2.2, 0.85
    box("StandFront", (cx, y0 - 2.5, 1.0), (width, 1.0, 2.0), concrete)          # 最前列の壁
    for i in range(n_tier):                                                       # ひな壇（観客）
        d = 3 + i * dep
        box(f"Tier{i}", (cx, y0 - d - dep / 2, 1.2 + i * rise), (width, dep, 0.5 + 0.0), crowd if i < 12 else seat)
    back_y = y0 - 3 - n_tier * dep
    top = 1.2 + n_tier * rise
    box("StandWall", (cx, back_y - 1, top / 2), (width, 2, top), concrete)       # ひな壇の後ろの壁
    for k in range(3):                                                            # 上のガラス張りの階
        z = top + 0.5 + k * 4.2
        box(f"Floor{k}", (cx, back_y - 6, z), (width, 14, 0.7), concrete)
        box(f"Glass{k}", (cx, back_y + 0.6, z + 2.1), (width - 4, 0.3, 3.4), glass)
    roof_z = top + 0.5 + 3 * 4.2 + 1.2
    box("Roof", (cx, back_y + 10, roof_z), (width + 8, 34, 1.0), roof)          # ひな壇の上に張り出す屋根
    box("RoofEdge", (cx, back_y + 27, roof_z - 0.6), (width + 8, 0.6, 1.6), white)
    for i in range(10):
        box(f"Pillar{i}", (sx * (xs0 + 8 + i * (width - 16) / 9), back_y - 0.5, roof_z / 2), (1.0, 1.0, roof_z), concrete)
    box("Apron", (cx, y0 + 2, 0.15), (width, 10, 0.3), concrete)
    # 内馬場の大型ビジョン（スタンドの向かい）
    box("VisionFrame", (sx * 20, R - 40, 9), (52, 2, 16), roof)
    box("Vision", (sx * 20, R - 41.2, 9.5), (48, 0.3, 13), mat("Screen", (0.05, 0.08, 0.12), 0.2, emit=0.3))
    # 木（外周と内馬場）
    tree_leaf = mat("Leaf", (0.07, 0.17, 0.06), 1.0, spec=0.05)
    trunk = mat("Trunk", (0.2, 0.13, 0.08), 1.0)
    me_l = bpy.data.meshes.new("Trees")
    bm = bmesh.new()
    def add_tree(x, y, h):
        r = h * 0.32
        ico = bmesh.ops.create_icosphere(bm, subdivisions=1, radius=r)
        for v in ico["verts"]:
            v.co = Vector((v.co.x + x, v.co.y + y, v.co.z * 1.25 + h * 0.62))
    for i in range(260):
        a = rnd.uniform(0, 2 * math.pi)
        rr = rnd.uniform(1.0, 1.25)
        x = (ST / 2 + R + 120) * rr * math.cos(a)
        y = (R + 110) * rr * math.sin(a)
        if y < -(R + 40) and abs(x) < ST / 2 + 120:
            continue                                     # スタンドの前には植えない
        add_tree(x, y, rnd.uniform(9, 16))
    for i in range(70):
        a = rnd.uniform(0, 2 * math.pi)
        x = (ST / 2 + R - 70) * 0.85 * math.cos(a)
        y = (R - 60) * 0.85 * math.sin(a)
        add_tree(x, y, rnd.uniform(6, 10))
    bm.to_mesh(me_l)
    bm.free()
    t = link(bpy.data.objects.new("Trees", me_l))
    t.data.materials.append(tree_leaf)
    # 遠くの山（京都盆地を囲む山並み）
    hill = mat("Hills", (0.2, 0.27, 0.3), 1.0, spec=0.0)
    me = bpy.data.meshes.new("Hills")
    bm = bmesh.new()
    seg, rad = 180, 6500
    ring = []
    for i in range(seg):
        a = 2 * math.pi * i / seg
        h = 70 + 170 * (0.5 + 0.5 * math.sin(3 * a + 1.0)) * (0.6 + 0.4 * math.sin(7 * a)) + rnd.uniform(0, 35)
        lo = bm.verts.new((rad * math.cos(a), rad * math.sin(a), -5))
        hi = bm.verts.new((rad * 0.97 * math.cos(a), rad * 0.97 * math.sin(a), h))
        ring.append((lo, hi))
    for i in range(seg):
        a, b = ring[i], ring[(i + 1) % seg]
        bm.faces.new((a[0], b[0], b[1], a[1]))
    bm.to_mesh(me)
    bm.free()
    hl = link(bpy.data.objects.new("Hills", me))
    hl.data.materials.append(hill)


# ---------------------------------------------------------------------------
# 馬と騎手
# ---------------------------------------------------------------------------
COATS = [(0.30, 0.13, 0.05), (0.42, 0.18, 0.06), (0.14, 0.07, 0.04), (0.36, 0.16, 0.07), (0.07, 0.05, 0.04),
         (0.55, 0.26, 0.09), (0.6, 0.58, 0.55)]
GALLOP_STRIDE = 7.0                      # 1完歩の距離[m]


def load_horse_template():
    """馬のモデル（骨＋メッシュ＋走るアクション）を読み込む。戻り値: (armature, [meshes], action, 元の長さ[m])"""
    path = SPEC.get("horse_model")
    before = set(bpy.data.objects)
    if path and path.endswith(".blend"):
        with bpy.data.libraries.load(path) as (src, dst):
            dst.objects = list(src.objects)
            dst.actions = list(src.actions)
        for o in dst.objects:
            if o and o.type in ("ARMATURE", "MESH"):
                link(o)
    elif path:
        bpy.ops.import_scene.gltf(filepath=path)
    new = [o for o in bpy.data.objects if o not in before]
    arm = next((o for o in new if o.type == "ARMATURE"), None)
    meshes = [o for o in new if o.type == "MESH"]
    act_name = SPEC.get("gallop_action", "Gallop")
    action = next((a for a in bpy.data.actions if act_name.lower() in a.name.lower()), None)
    return arm, meshes, action, new


def jockey_parts(silk, cap):
    """騎手（前傾した姿勢）。原点は鞍の上"""
    silk_m = mat(f"Silk_{silk}", tuple(silk), 0.45, spec=0.5)
    cap_m = mat(f"Cap_{cap}", tuple(cap), 0.35, spec=0.6)
    skin = mat("Breeches", (0.9, 0.9, 0.88), 0.6)
    boots = mat("Boots", (0.03, 0.03, 0.03), 0.4)
    parts = []
    def ell(loc, sc, m, rot=(0, 0, 0)):
        bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=10, location=loc, rotation=rot)
        o = bpy.context.active_object
        o.scale = sc
        o.data.materials.append(m)
        bpy.ops.object.shade_smooth()
        parts.append(o)
        return o
    ell((0.05, 0, 0.42), (0.36, 0.2, 0.14), silk_m, (0, -0.35, 0))      # 胴（前傾）
    ell((0.4, 0, 0.58), (0.11, 0.1, 0.12), mat("Face", (0.8, 0.6, 0.5), 0.6))
    ell((0.41, 0, 0.64), (0.13, 0.12, 0.09), cap_m)                      # 帽子（枠色）
    for side in (-1, 1):
        ell((0.28, side * 0.17, 0.36), (0.22, 0.05, 0.05), silk_m, (0, 0.5, side * 0.15))   # 腕
        ell((-0.02, side * 0.2, 0.17), (0.2, 0.07, 0.08), skin, (0, -0.3, 0))               # もも
        ell((0.12, side * 0.21, 0.0), (0.06, 0.06, 0.16), boots, (0, 0.4, 0))               # すね
    return parts


def coat_mats(ci):
    """毛色ごとの Main / Main_Dark / Main_Light / Hair"""
    base = COATS[ci % len(COATS)]
    grey = ci % len(COATS) == 6
    out = {"Main": mat(f"Coat{ci}", base, 0.42, spec=0.5),
           "Main_Dark": mat(f"Coat{ci}D", tuple(c * 0.55 for c in base), 0.45, spec=0.45),
           "Main_Light": mat(f"Coat{ci}L", tuple(min(c * 1.45, 1) for c in base), 0.42, spec=0.5)}
    if grey:
        out["Hair"] = mat("HairGrey", (0.45, 0.44, 0.42), 0.6)
    return out


def build_horses():
    """各馬（骨付きモデルを複製）。戻り値は位置を動かすオブジェクトのリスト"""
    from mathutils import Matrix
    arm, meshes, action, new = load_horse_template()
    k = SPEC.get("horse_scale", 0.5)
    horses = []
    for i, h in enumerate(SPEC["horses"]):
        if arm:
            a2 = arm.copy()
            a2.data = arm.data.copy()
            a2.animation_data_clear()
            link(a2)
            a2.scale = (k, k, k)
            cm = coat_mats(h["coat"])
            for m in meshes:
                m2 = m.copy()
                m2.data = m.data.copy()
                link(m2)
                m2.parent = a2
                for mod in m2.modifiers:
                    if mod.type == "ARMATURE":
                        mod.object = a2
                for slot in m2.material_slots:
                    key = slot.material.name.split(".")[0] if slot.material else ""
                    if key in cm:
                        slot.link = "OBJECT"
                        slot.material = cm[key]
            ad = a2.animation_data_create()
            if action:
                idle = next((a for a in bpy.data.actions if a.name.split(".")[0] == SPEC.get("idle_action", "Idle")),
                            action)
                tr0 = ad.nla_tracks.new()                      # 下: 立ち止まり（Idle）
                st0 = tr0.strips.new("idle", 0, idle)
                st0.use_animated_time = True
                st0.extrapolation = "HOLD"
                st0.frame_end = 100000          # 動画全体をおおう（範囲外では時刻のアニメーションが効かない）
                tr = ad.nla_tracks.new()                       # 上: 走り（Gallop）。止まっている間は影響 0
                st = tr.strips.new("gallop", 0, action)
                st.use_animated_time = True
                st.use_animated_influence = True
                st.extrapolation = "HOLD"
                st.frame_end = 100000
                st.blend_type = "REPLACE"
            root = a2
        else:
            root = simple_horse(f"H{i}", mat(f"Coat{h['coat']}", COATS[h["coat"] % len(COATS)], 0.45))
        # 騎手・ゼッケン（背中の骨に付けて、走りの上下動に合わせて動かす）
        holder = bpy.data.objects.new(f"Rider{i}", None)
        link(holder)
        for p in jockey_parts(h["silk"], h["cap"]):
            p.parent = holder
        num = bpy.data.curves.new(f"Num{i}", "FONT")
        num.body = str(h["num"])
        num.size = 0.24
        num.align_x = "CENTER"
        num.align_y = "CENTER"
        ink = mat("NumInk", (0.02, 0.02, 0.02), 0.5)
        clothm = mat("ClothWhite", (0.92, 0.92, 0.92), 0.6)
        for side in (-1, 1):
            c = box(f"Cloth{i}{side}", (0, 0, 0), (0.6, 0.02, 0.42), clothm)
            c.parent = holder
            c.location = (-0.12, side * 0.36, -0.22)
            n2 = link(bpy.data.objects.new(f"Num{i}{side}", num))
            n2.data.materials.append(ink)
            n2.parent = holder
            n2.location = (-0.12, side * 0.375, -0.24)
            n2.rotation_euler = (math.pi / 2, 0, 0 if side == -1 else math.pi)
        if arm:
            bone = SPEC.get("rider_bone", "Torso2")
            holder.parent = root
            holder.parent_type = "BONE"
            holder.parent_bone = bone
            bpy.context.view_layer.update()
            pos = Vector(SPEC.get("rider_pos", [0.0, -0.25, 3.0]))
            holder.matrix_world = (root.matrix_world @ Matrix.Translation(pos)
                                   @ Matrix.Rotation(-math.pi / 2, 4, "Z") @ Matrix.Scale(1 / k, 4))
        else:
            holder.parent = root
            holder.location = (0, 0, 1.55)
        horses.append(root)
    if arm:
        for o in new:
            bpy.data.objects.remove(o, do_unlink=True)
    return horses


def simple_horse(name, coat):
    """モデルが無いときの簡易の馬"""
    root = link(bpy.data.objects.new(name, None))
    def ell(loc, sc, rot=(0, 0, 0)):
        bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=10, location=loc, rotation=rot)
        o = bpy.context.active_object
        o.scale = sc
        o.data.materials.append(coat)
        o.parent = root
        bpy.ops.object.shade_smooth()
    ell((0, 0, 1.3), (1.0, 0.33, 0.38))
    ell((0.95, 0, 1.75), (0.5, 0.14, 0.16), (0, -0.9, 0))
    ell((1.35, 0, 2.0), (0.32, 0.12, 0.14), (0, 0.5, 0))
    for dx in (0.65, -0.65):
        for side in (-0.18, 0.18):
            ell((dx, side, 0.55), (0.07, 0.07, 0.55))
    return root


# ---------------------------------------------------------------------------
# アニメーションとカメラ
# ---------------------------------------------------------------------------
def animate(horses):
    sc = bpy.context.scene
    frames = SPEC["frames"]
    cam_data = bpy.data.cameras.new("Cam")
    cam = link(bpy.data.objects.new("Cam", cam_data))
    sc.camera = cam
    cam_data.clip_end = 12000
    cam_data.sensor_width = 36
    build_tags(cam)
    ground_z = SPEC.get("horse_ground", 0.0)
    cyc = SPEC.get("gallop_frames", 18.0)
    stride = SPEC.get("gallop_stride", GALLOP_STRIDE)
    want = sorted({g for f in PICK for g in range(max(f - 1, 0), min(f + 2, len(frames)))})
    for f in want:
        fr = frames[f]
        for i, ob in enumerate(horses):
            s, lat = fr["s"][i], fr["lat"][i]
            p = P(s, lat, ground_z)
            ob.location = p
            hd = heading(s, lat)
            fa, fb = frames[max(f - 1, 0)], frames[min(f + 1, len(frames) - 1)]
            if (fb is not fa and abs(fb["tau"] - fa["tau"]) < 0.6 and fb["s"][i] - fa["s"][i] > 0.05
                    and fa["run"][i] and fb["run"][i]):
                a, b = P(fa["s"][i], fa["lat"][i]), P(fb["s"][i], fb["lat"][i])
                hd = math.atan2(b.y - a.y, b.x - a.x)        # 斜めに進むときは体もその向きに
            ob.rotation_euler = (0, 0, hd + SPEC.get("model_yaw", 0.0))
            ob.keyframe_insert("location", frame=f + 1)
            ob.keyframe_insert("rotation_euler", frame=f + 1)
            if ob.animation_data and len(ob.animation_data.nla_tracks) >= 2:
                idle = ob.animation_data.nla_tracks[0].strips[0]
                gal = ob.animation_data.nla_tracks[1].strips[0]
                run = fr.get("run", [1] * len(horses))[i]
                gal.influence = 1.0 if run else 0.0
                gal.strip_time = (fr["dist"][i] / stride * cyc) % cyc if run else 0.0
                idle.strip_time = (f * 0.5 + i * 7) % max(idle.action_frame_end - 1, 1)
                for stp, prop in ((gal, "influence"), (gal, "strip_time"), (idle, "strip_time")):
                    stp.keyframe_insert(prop, frame=f + 1)
        c = fr["cam"]
        for i, tg in enumerate(TAGS):
            hp = P(fr["s"][i], fr["lat"][i], 0.0)
            dist_c = (Vector(c["loc"]) - Vector((hp.x, hp.y, 2.6))).length
            k = 1.15 * dist_c / max(c.get("lens", 50), 1) if fr.get("tags") else 0.0
            lane = (fr["lat"][i] - 1.4) / 1.235          # 横に並んだ馬の名札は上下にずらす（列が変わると滑らかに）
            tg.location = (hp.x, hp.y, 2.55 + 0.62 * k * (0.5 - 0.5 * math.cos(math.pi * lane)))
            tg.scale = (k, k, k)
            tg.keyframe_insert("location", frame=f + 1)
            tg.keyframe_insert("scale", frame=f + 1)
        cam.location = Vector(c["loc"])
        tgt = Vector(c["look"])
        cam.rotation_euler = (tgt - cam.location).to_track_quat("-Z", "Y").to_euler()
        cam_data.lens = c.get("lens", 50)
        cam.keyframe_insert("location", frame=f + 1)
        cam.keyframe_insert("rotation_euler", frame=f + 1)
        cam_data.keyframe_insert("lens", frame=f + 1)
    for ob in [cam, cam_data] + horses + TAGS:
        ad = ob.animation_data
        if ad and ad.action:
            for fc in ad.action.fcurves:
                for kp in fc.keyframe_points:
                    kp.interpolation = "LINEAR"
        if ob in horses and ob.animation_data:
            for tr in ob.animation_data.nla_tracks:
                pass


def build_gate():
    """スタートのゲート（発馬機）。各馬のスタート時の横位置の間に仕切りを立てる"""
    fr = SPEC["frames"][0]
    lats = sorted(fr["lat"])
    s0 = C["start_s"]
    hd = heading(s0)
    green = mat("GateGreen", (0.05, 0.3, 0.16), 0.5)
    white = mat("Rail", (0.92, 0.92, 0.9), 0.4)
    bounds = [lats[0] - 0.65] + [(a + b) / 2 for a, b in zip(lats, lats[1:])] + [lats[-1] + 0.65]
    for j, off in enumerate(bounds):
        p = P(s0, off)
        box(f"GatePost{j}", (p.x, p.y, 1.35), (3.2, 0.14, 2.7), green, rot_z=hd)
    a, b = P(s0, bounds[0]), P(s0, bounds[-1])
    mid = (a + b) / 2
    span = (b - a).length
    box("GateBeam", (mid.x, mid.y, 2.85), (0.5, span + 0.6, 0.4), white, rot_z=hd)
    box("GateBeamF", ((P(s0 + 1.5, (bounds[0] + bounds[-1]) / 2)).x, (P(s0 + 1.5, (bounds[0] + bounds[-1]) / 2)).y, 2.85),
        (0.3, span + 0.6, 0.3), green, rot_z=hd)


TAGS, TAG_ROW = [], []
WAKU_RGB = [(0.95, 0.95, 0.95), (0.05, 0.05, 0.05), (0.85, 0.08, 0.08), (0.1, 0.35, 0.85), (0.98, 0.84, 0.1),
            (0.1, 0.6, 0.2), (1.0, 0.5, 0.08), (1.0, 0.6, 0.72)]


def emit_mat(name, color, alpha=1.0):
    """照明の影響を受けない色（名札用）"""
    m = bpy.data.materials.get(name)
    if m:
        return m
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs[0].default_value = (*color, 1)
    em.inputs[1].default_value = 1.0
    if alpha < 1:
        tr = nt.nodes.new("ShaderNodeBsdfTransparent")
        mix = nt.nodes.new("ShaderNodeMixShader")
        mix.inputs[0].default_value = alpha
        nt.links.new(tr.outputs[0], mix.inputs[1])
        nt.links.new(em.outputs[0], mix.inputs[2])
        nt.links.new(mix.outputs[0], out.inputs[0])
        m.blend_method = "BLEND"
    else:
        nt.links.new(em.outputs[0], out.inputs[0])
    m.shadow_method = "NONE"
    return m


def build_tags(cam):
    """各馬の上に浮かぶ名札（枠色の四角に馬番＋馬名）。いつも画面に正対し、同じくらいの大きさに見える"""
    font = None
    fp = SPEC.get("label_font")
    if fp:
        try:
            font = bpy.data.fonts.load(fp)
        except Exception:      # noqa: BLE001
            font = None
    first = SPEC["frames"][0]
    order = sorted(range(len(SPEC["horses"])), key=lambda i: first["lat"][i])
    for i, h in enumerate(SPEC["horses"]):
        root = link(bpy.data.objects.new(f"Tag{i}", None))
        con = root.constraints.new("COPY_ROTATION")
        con.target = cam
        name = h["name"]
        nw = 0.36 * len(name) + 0.3
        sq = 0.5
        total = sq + nw
        x0 = -total / 2

        def plane(nm, w, hgt, x, m, z=0.0):
            bpy.ops.mesh.primitive_plane_add(size=1, location=(0, 0, 0))
            o = bpy.context.active_object
            o.name = nm
            o.scale = (w, hgt, 1)
            o.location = (x + w / 2, hgt / 2, z)
            o.data.materials.append(m)
            o.parent = root
            return o
        w = h["waku"] - 1
        plane(f"TagSq{i}", sq, sq, x0, emit_mat(f"Waku{w}", WAKU_RGB[w]))
        plane(f"TagBg{i}", nw, sq, x0 + sq, emit_mat("TagBg", (0.03, 0.04, 0.05), 0.82))
        for txt, x, col, align in ((str(h["num"]), x0 + sq / 2, (0.02, 0.02, 0.02) if w in (0, 4, 6, 7) else (1, 1, 1),
                                    "CENTER"), (name, x0 + sq + 0.14, (1, 1, 1), "LEFT")):
            cu = bpy.data.curves.new(f"TagT{i}{align}", "FONT")
            cu.body = txt
            if font:
                cu.font = font
            cu.size = 0.36
            cu.align_x = align
            cu.align_y = "CENTER"
            o = link(bpy.data.objects.new(f"TagT{i}{align}", cu))
            o.data.materials.append(emit_mat(f"TagInk{col}", col))
            o.location = (x, sq / 2, 0.01)
            o.parent = root
        if w == 0:                                     # 白い枠は縁取り
            plane(f"TagSqEdge{i}", sq + 0.06, sq + 0.06, x0 - 0.03, emit_mat("TagEdge", (0.5, 0.5, 0.5)), z=-0.005)
            bpy.context.active_object.location.y -= 0.03
        root.scale = (0, 0, 0)
        TAGS.append(root)
        TAG_ROW.append(order.index(i))


def main():
    build_world()
    build_course()
    build_gate()
    horses = build_horses()
    animate(horses)
    sc = bpy.context.scene
    for f in PICK:
        sc.frame_set(f + 1)
        sc.render.filepath = f"{OUT}/f{f:05d}.jpg"
        bpy.ops.render.render(write_still=True)


main()
