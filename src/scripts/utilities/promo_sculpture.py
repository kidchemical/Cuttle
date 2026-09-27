"""White-studio abstract sculpture for the Cuttle promo intro/outro (Blender 4.0 EEVEE)."""
from __future__ import annotations

import math
import sys
from pathlib import Path

import bpy

OUT = Path(sys.argv[sys.argv.index("--") + 1]) if "--" in sys.argv else Path("/tmp/cuttle-sculpt")
OUT.mkdir(parents=True, exist_ok=True)
FRAMES = 600  # 20s @ 30fps — intro uses 1–240, outro 241–600
W, H, FPS = 1920, 1080, 30

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.engine = "BLENDER_EEVEE"
scene.render.resolution_x = W
scene.render.resolution_y = H
scene.render.fps = FPS
scene.frame_start = 1
scene.frame_end = FRAMES
scene.render.filepath = str(OUT / "f")
scene.render.image_settings.file_format = "PNG"
scene.render.film_transparent = False
eevee = scene.eevee
eevee.taa_render_samples = 24
eevee.use_gtao = True
eevee.use_bloom = True
eevee.bloom_threshold = 1.2
eevee.bloom_intensity = 0.04
eevee.use_ssr = True
eevee.use_ssr_refraction = True

world = bpy.data.worlds.new("studio")
scene.world = world
world.use_nodes = True
bg = world.node_tree.nodes["Background"]
bg.inputs["Color"].default_value = (0.97, 0.97, 0.98, 1)
bg.inputs["Strength"].default_value = 0.85

# Ground plane (soft shadow catcher feel)
bpy.ops.mesh.primitive_plane_add(size=18, location=(0, 0, -1.15))
ground = bpy.context.object
mat_g = bpy.data.materials.new("ground")
mat_g.use_nodes = True
nt = mat_g.node_tree
nt.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.96, 0.96, 0.97, 1)
nt.nodes["Principled BSDF"].inputs["Roughness"].default_value = 0.55
ground.data.materials.append(mat_g)


def ceramic(name, color, transmission=0.35):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    p = m.node_tree.nodes["Principled BSDF"]
    p.inputs["Base Color"].default_value = (*color, 1)
    p.inputs["Roughness"].default_value = 0.18
    for key, val in (
        ("Specular IOR Level", 0.65),
        ("Specular", 0.65),
        ("Coat Weight", 0.4),
        ("Clearcoat", 0.4),
        ("Coat Roughness", 0.08),
        ("Clearcoat Roughness", 0.08),
        ("Transmission Weight", transmission),
        ("Transmission", transmission),
        ("IOR", 1.45),
    ):
        if key in p.inputs:
            p.inputs[key].default_value = val
    return m


lilac = ceramic("lilac", (0.55, 0.48, 0.92), 0.42)
ink = ceramic("ink", (0.18, 0.16, 0.28), 0.12)
milk = ceramic("milk", (0.93, 0.93, 0.96), 0.2)


def smooth_mesh(obj, levels=2):
    sub = obj.modifiers.new("sub", "SUBSURF")
    sub.levels = levels
    sub.render_levels = levels
    bpy.ops.object.shade_smooth()


# Boolean-ish cluster: overlapping spheres + elongated torus, read as a single sculpture
parts = []
bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=5, radius=1.05, location=(0.05, 0.0, 0.15))
a = bpy.context.object
a.scale = (1.15, 0.82, 0.95)
a.data.materials.append(lilac)
parts.append(a)

bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=5, radius=0.72, location=(0.85, -0.15, 0.35))
b = bpy.context.object
b.scale = (1.0, 0.7, 1.15)
b.data.materials.append(milk)
parts.append(b)

bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=4, radius=0.48, location=(-0.7, 0.35, 0.55))
c = bpy.context.object
c.scale = (0.9, 1.2, 0.75)
c.data.materials.append(ink)
parts.append(c)

bpy.ops.mesh.primitive_torus_add(major_radius=0.95, minor_radius=0.16, location=(0.1, 0.2, 0.2), rotation=(0.7, 0.2, 0.4))
d = bpy.context.object
d.data.materials.append(lilac)
parts.append(d)

bpy.ops.mesh.primitive_torus_add(major_radius=0.62, minor_radius=0.11, location=(-0.2, -0.4, 0.4), rotation=(1.2, 0.5, -0.3))
e = bpy.context.object
e.data.materials.append(milk)
parts.append(e)

for obj in parts:
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    smooth_mesh(obj, 2)
    obj.select_set(False)

# Slow breathing + orbit via empty
bpy.ops.object.empty_add(location=(0, 0, 0.2))
pivot = bpy.context.object
for obj in parts:
    obj.parent = pivot
pivot.rotation_mode = "XYZ"
pivot.keyframe_insert("rotation_euler", frame=1)
pivot.rotation_euler = (0.15, 0.4, math.tau * 0.55)
pivot.keyframe_insert("rotation_euler", frame=FRAMES)
for fcu in pivot.animation_data.action.fcurves:
    for kp in fcu.keyframe_points:
        kp.interpolation = "LINEAR"

# Lights — soft studio, purple rim
bpy.ops.object.light_add(type="AREA", location=(3.2, -2.4, 4.0))
key = bpy.context.object
key.data.energy = 420
key.data.size = 4.5
key.rotation_euler = (0.7, 0.2, 0.6)

bpy.ops.object.light_add(type="AREA", location=(-3.8, 1.5, 2.4))
fill = bpy.context.object
fill.data.energy = 180
fill.data.size = 5.0
fill.data.color = (0.72, 0.68, 1.0)

bpy.ops.object.light_add(type="AREA", location=(0.2, 4.0, 1.2))
rim = bpy.context.object
rim.data.energy = 260
rim.data.size = 2.2
rim.data.color = (0.85, 0.8, 1.0)

# Camera orbit
bpy.ops.object.empty_add(location=(0, 0, 0.25))
cam_pivot = bpy.context.object
bpy.ops.object.camera_add(location=(0, -7.4, 2.35))
cam = bpy.context.object
cam.data.lens = 45
cam.parent = cam_pivot
track = cam.constraints.new("TRACK_TO")
track.target = pivot
track.track_axis = "TRACK_NEGATIVE_Z"
track.up_axis = "UP_Y"
scene.camera = cam
cam_pivot.rotation_mode = "XYZ"
cam_pivot.keyframe_insert("rotation_euler", frame=1)
cam_pivot.rotation_euler.z = math.tau * 0.42
cam_pivot.keyframe_insert("rotation_euler", frame=FRAMES)
for fcu in cam_pivot.animation_data.action.fcurves:
    for kp in fcu.keyframe_points:
        kp.interpolation = "LINEAR"

# Gentle dolly
cam.location.y = -7.4
cam.keyframe_insert("location", frame=1)
cam.location.y = -6.6
cam.keyframe_insert("location", frame=FRAMES)

bpy.ops.render.render(animation=True)
print("SCULPTURE_DONE", OUT)
