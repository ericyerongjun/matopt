#!/usr/bin/env python3
"""Build MatOpt's tutor avatar from VRoid's CC0 HairSample_Male sample.

The tutor is a fan-made tribute caricature, not affiliated with or endorsed
by the person, with no logos and no cloned voice:

- ``andrej``: short, loose dark-brown fringe, lighter skin, a charcoal hoodie.

Only material parameters and a few textures change; the rig, expressions and
lip-sync blendshapes are VRoid's originals.

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
    "andrej": {
        "title": "MatOpt Tutor (Andrej Karpathy tribute)",
        "hair_ramp": ((34, 24, 18), (138, 102, 76)),
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path, help="VRoid HairSample_Male.vrm (CC0)")
    parser.add_argument("output", type=Path)
    parser.add_argument("--style", choices=sorted(STYLES), default="andrej")
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
    replace_image(gltf, binary, texture["source"], plain_tee(image_bytes(gltf, binary, texture["source"])))

    if preset.get("hair_ramp"):
        for props in vrm["materialProperties"]:
            if props["name"].endswith("_HAIR") or "_HAIR_" in props["name"]:
                index = gltf["textures"][props["textureProperties"]["_MainTex"]]["source"]
                replace_image(gltf, binary, index, recolor(image_bytes(gltf, binary, index), *preset["hair_ramp"], stretch=True))

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
