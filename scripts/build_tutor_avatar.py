#!/usr/bin/env python3
"""Build MatOpt's tutor avatars from VRoid's CC0 Base_Male sample.

Each style is a fan-made tribute caricature, not affiliated with or endorsed
by the person or their employer, with no logos and no cloned voice:

- ``jensen``: swept-back silver hair, open black leather jacket over a black T-shirt.
- ``andrej``: short, loose dark-brown fringe, lighter skin, a charcoal hoodie
  (built from HairSample_Male, whose hair and hooded top suit the look).

Only material parameters, a few textures and one mesh primitive change; the
rig, expressions and lip-sync blendshapes are VRoid's originals.

    curl -sSfLO https://opengameart.org/sites/default/files/base_male.zip && unzip base_male.zip
    python3 scripts/build_tutor_avatar.py Base_Male.vrm frontend/public/avatars/tutor-jensen.vrm --style jensen
    curl -sSfLO https://opengameart.org/sites/default/files/hairsample_male.zip && unzip hairsample_male.zip
    python3 scripts/build_tutor_avatar.py HairSample_Male.vrm frontend/public/avatars/tutor-andrej.vrm --style andrej
"""
from __future__ import annotations

import argparse
import io
import json
import struct
from pathlib import Path

from PIL import Image, ImageStat

# MToon colours (linear RGBA) per style. Hair is a greyscale texture tinted by
# _Color, so the shade colour sets how dark its strands read.
STYLES = {
    "jensen": {
        "title": "MatOpt Tutor (Jensen Huang tribute)",
        "top": "leather_jacket", "sleeves": True,
        "materials": {
            "HAIR": {"_Color": [0.88, 0.88, 0.90, 1], "_ShadeColor": [0.30, 0.30, 0.34, 1]},
            # Leather: dark texture, deep shade and a cool rim highlight for the sheen.
            "Tops": {"_Color": [1, 1, 1, 1], "_ShadeColor": [0.42, 0.42, 0.46, 1], "_RimColor": [0.55, 0.56, 0.62, 1],
                     "float": {"_RimFresnelPower": 3.5, "_RimLift": 0.04, "_RimLightingMix": 0.7}},
            "Bottoms": {"_Color": [0.12, 0.12, 0.14, 1], "_ShadeColor": [0.05, 0.05, 0.06, 1]},
            "Shoes": {"_Color": [0.10, 0.10, 0.11, 1], "_ShadeColor": [0.04, 0.04, 0.05, 1]},
            "SKIN": {"_Color": [1.00, 0.84, 0.70, 1], "_ShadeColor": [0.93, 0.66, 0.52, 1]},
            "FaceBrow": {"_Color": [0.35, 0.33, 0.33, 1]},
        },
    },
    "andrej": {
        "title": "MatOpt Tutor (Andrej Karpathy tribute)",
        "top": "plain_tee", "sleeves": False, "hair_ramp": ((34, 24, 18), (138, 102, 76)),
        "materials": {
            "HAIR": {"_Color": [1, 1, 1, 1], "_ShadeColor": [0.5, 0.45, 0.42, 1]},
            "Tops": {"_Color": [1, 1, 1, 1], "_ShadeColor": [0.6, 0.6, 0.64, 1]},
            "Bottoms": {"_Color": [0.20, 0.23, 0.30, 1], "_ShadeColor": [0.09, 0.10, 0.14, 1]},
            "Shoes": {"_Color": [0.85, 0.85, 0.86, 1], "_ShadeColor": [0.55, 0.55, 0.58, 1]},
            "SKIN": {"_Color": [1.00, 0.93, 0.86, 1], "_ShadeColor": [0.93, 0.74, 0.68, 1]},
            "FaceBrow": {"_Color": [0.22, 0.16, 0.13, 1]},
        },
    },
}
REMOVE_MATERIALS = ("Accessory_Tie",)


def read_glb(path: Path) -> tuple[dict, bytearray]:
    data = path.read_bytes()
    magic, version, _ = struct.unpack("<4sII", data[:12])
    if magic != b"glTF" or version != 2:
        raise SystemExit("Not a glTF 2.0 binary (VRM) file.")
    json_len, json_type = struct.unpack("<I4s", data[12:20])
    gltf = json.loads(data[20:20 + json_len])
    bin_len, bin_type = struct.unpack("<I4s", data[20 + json_len:28 + json_len])
    if json_type != b"JSON" or bin_type != b"BIN\x00":
        raise SystemExit("Unexpected GLB chunk layout.")
    return gltf, bytearray(data[28 + json_len:28 + json_len + bin_len])


def write_glb(path: Path, gltf: dict, binary: bytearray) -> None:
    gltf["buffers"][0]["byteLength"] = len(binary)
    payload = json.dumps(gltf, ensure_ascii=False, separators=(",", ":")).encode()
    payload += b" " * (-len(payload) % 4)
    binary += b"\x00" * (-len(binary) % 4)
    total = 12 + 8 + len(payload) + 8 + len(binary)
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, total) + struct.pack("<I4s", len(payload), b"JSON") + payload
                     + struct.pack("<I4s", len(binary), b"BIN\x00") + bytes(binary))


def image_bytes(gltf: dict, binary: bytearray, index: int) -> bytes:
    view = gltf["bufferViews"][gltf["images"][index]["bufferView"]]
    start = view.get("byteOffset", 0)
    return bytes(binary[start:start + view["byteLength"]])


def replace_image(gltf: dict, binary: bytearray, index: int, png: bytes) -> None:
    binary += b"\x00" * (-len(binary) % 4)
    gltf["bufferViews"].append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(png)})
    binary += png
    gltf["images"][index]["bufferView"] = len(gltf["bufferViews"]) - 1
    gltf["images"][index]["mimeType"] = "image/png"


def recolor(png: bytes, dark: tuple, light: tuple, stretch: bool = False) -> bytes:
    """Map a texture's shading onto a dark-to-light colour ramp, keeping alpha.

    ``stretch`` spreads a dark texture's luminance over the full ramp first, so
    near-black hair can become brown with its strand detail intact.
    """
    image = Image.open(io.BytesIO(png)).convert("RGBA")
    pixels = image.load()
    low, high = 0.0, 1.0
    if stretch:  # luminance range of the visible (non-transparent) pixels
        dark_end, light_end = ImageStat.Stat(image.convert("L"), mask=image.getchannel("A")).extrema[0]
        low, high = dark_end / 255, light_end / 255
    span = max(high - low, 1e-6)
    for y in range(image.height):
        for x in range(image.width):
            r, g, b, a = pixels[x, y]
            lum = min(1.0, max(0.0, ((0.299 * r + 0.587 * g + 0.114 * b) / 255 - low) / span))
            pixels[x, y] = tuple(int(d + (l - d) * lum) for d, l in zip(dark, light)) + (a,)
    out = io.BytesIO()
    image.save(out, "PNG", optimize=True)
    return out.getvalue()


def brown_iris(png: bytes) -> bytes:
    """Recolour the blue iris to dark brown, keeping its shading and alpha."""
    return recolor(png, (38, 22, 14), (122, 82, 52))


def leather_jacket(png: bytes) -> bytes:
    """Repaint the vest-and-shirt top as an open black leather jacket over a black T-shirt.

    Layout (512-unit coordinates): the front panel is the centre of the lower
    block with the neckline at x≈256; the collar is top centre. Shading comes
    from the original texture's luminance so folds survive.
    """
    image = Image.open(io.BytesIO(png)).convert("RGBA")
    unit = image.width / 512
    pixels = image.load()
    leather, tee, edge = (52, 52, 58), (24, 24, 26), (110, 110, 118)
    for y in range(image.height):
        for x in range(image.width):
            r, g, b, a = pixels[x, y]
            u, v = x / unit, y / unit
            lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255
            front_strip = 226 <= u <= 286 and v >= 150 and 150 <= u <= 360
            if front_strip and (u < 229 or u > 283):
                base, shade = edge, 1.0                           # jacket opening edges
            elif front_strip:
                base, shade = tee, 0.85 + 0.3 * lum              # matte T-shirt
            else:
                gloss = 0.18 * max(0.0, lum - 0.6)               # leather catches light on highlights
                base, shade = leather, 0.62 + 0.38 * lum + gloss  # bright source areas (shirt) stay dark
            pixels[x, y] = tuple(min(255, int(c * shade)) for c in base) + (a,)
    out = io.BytesIO()
    image.save(out, "PNG", optimize=True)
    return out.getvalue()


def plain_tee(png: bytes) -> bytes:
    """Repaint a top in plain charcoal cotton, keeping its fold shading."""
    image = Image.open(io.BytesIO(png)).convert("RGBA")
    pixels = image.load()
    cotton = (66, 68, 74)
    for y in range(image.height):
        for x in range(image.width):
            r, g, b, a = pixels[x, y]
            lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255
            pixels[x, y] = tuple(min(255, int(c * (0.7 + 0.4 * lum))) for c in cotton) + (a,)
    out = io.BytesIO()
    image.save(out, "PNG", optimize=True)
    return out.getvalue()


def leather_sleeves(png: bytes) -> bytes:
    """Paint long black leather sleeves over the arm areas of the body texture.

    In VRoid's body layout the arms are the upper halves of the two outer
    columns; hands and neck live elsewhere and stay skin. The original
    luminance is kept so the sleeves still show the arms' shading.
    """
    image = Image.open(io.BytesIO(png)).convert("RGBA")
    unit = image.width / 512
    leather = (40, 40, 45)
    pixels = image.load()
    for x0, x1 in ((0, 118), (392, 512)):
        for y in range(0, int(246 * unit)):
            for x in range(int(x0 * unit), int(x1 * unit)):
                r, g, b, a = pixels[x, y]
                shade = 0.55 + 0.6 * (0.299 * r + 0.587 * g + 0.114 * b) / 255
                pixels[x, y] = tuple(min(255, int(c * shade)) for c in leather) + (a,)
    out = io.BytesIO()
    image.save(out, "PNG", optimize=True)
    return out.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path, help="VRoid Base_Male.vrm (jensen) or HairSample_Male.vrm (andrej), both CC0")
    parser.add_argument("output", type=Path)
    parser.add_argument("--style", choices=sorted(STYLES), default="jensen")
    args = parser.parse_args()
    preset = STYLES[args.style]
    gltf, binary = read_glb(args.source)
    vrm = gltf["extensions"]["VRM"]

    for props in vrm["materialProperties"]:
        for key, style in preset["materials"].items():
            if key in props["name"]:
                for name, value in style.items():
                    if name == "float":
                        props["floatProperties"].update(value)
                    else:
                        props["vectorProperties"][name] = value
                if props["name"].endswith("_HAIR") or "Tops" in props["name"]:
                    props["keywordMap"]["MTOON_OUTLINE_COLOR_FIXED"] = True
                    props["vectorProperties"]["_OutlineColor"] = [0.08, 0.08, 0.09, 1]

    iris = next(p for p in vrm["materialProperties"] if "EyeIris" in p["name"])
    texture = gltf["textures"][iris["textureProperties"]["_MainTex"]]
    replace_image(gltf, binary, texture["source"], brown_iris(image_bytes(gltf, binary, texture["source"])))

    tops = next(p for p in vrm["materialProperties"] if "Tops" in p["name"])
    texture = gltf["textures"][tops["textureProperties"]["_MainTex"]]
    repaint = {"leather_jacket": leather_jacket, "plain_tee": plain_tee}[preset["top"]]
    replace_image(gltf, binary, texture["source"], repaint(image_bytes(gltf, binary, texture["source"])))

    if preset.get("hair_ramp"):
        for props in vrm["materialProperties"]:
            if props["name"].endswith("_HAIR") or "_HAIR_" in props["name"]:
                index = gltf["textures"][props["textureProperties"]["_MainTex"]]["source"]
                replace_image(gltf, binary, index, recolor(image_bytes(gltf, binary, index), *preset["hair_ramp"], stretch=True))

    if preset["sleeves"]:
        body = next(p for p in vrm["materialProperties"] if "Body_00_SKIN" in p["name"])
        texture = gltf["textures"][body["textureProperties"]["_MainTex"]]
        replace_image(gltf, binary, texture["source"], leather_sleeves(image_bytes(gltf, binary, texture["source"])))

    removed = {i for i, material in enumerate(gltf["materials"]) if any(name in material["name"] for name in REMOVE_MATERIALS)}
    for mesh in gltf["meshes"]:
        mesh["primitives"] = [p for p in mesh["primitives"] if p.get("material") not in removed]

    vrm["meta"].update({
        "title": preset["title"],
        "author": f"MatOpt, from VRoid Project's {args.source.stem} (CC0)",
        "contactInformation": "",
        "reference": "https://opengameart.org/content/vroid-studio-cc0-models",
        "licenseName": "CC0",
    })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_glb(args.output, gltf, binary)
    print(f"wrote {args.output} ({args.output.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
