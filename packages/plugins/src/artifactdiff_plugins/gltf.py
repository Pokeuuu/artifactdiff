"""glTF 2.0 / GLB comparison: scene graph, transforms, meshes, geometry, materials, textures, bounds.

The file is parsed directly (GLB container + glTF JSON + accessors via NumPy) instead of through a
scene loader, so that names, the node hierarchy, materials and accessor data are all preserved.

Matching rules:
* Nodes, meshes and materials are matched by name (unnamed ones by index, with a warning).
* A removed and an added node are reported as a *rename* only when exactly one candidate pair has
  identical geometry hash and local transform (confidence 0.9; the evidence says why).
* Geometry is compared by a SHA-256 hash of vertex positions and indices, so equal vertex/face
  counts never imply equal meshes; when counts match, the maximum vertex displacement is reported.

Security: external buffer/image URIs are resolved only relative to the model's own directory
(no absolute paths, no ``..``, no network). Missing data is reported, never fetched.
"""

from __future__ import annotations

import base64
import hashlib
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote

from pydantic import Field

from artifactdiff.errors import PluginInputError, ResourceLimitError
from artifactdiff.files import ArtifactFile
from artifactdiff.models import ChangeCategory, ChangeKind, ChangeSeverity
from artifactdiff.plugin import ArtifactPlugin, ComparisonContext, PluginOptions
from artifactdiff_plugins._common import png_bytes

if TYPE_CHECKING:
    import numpy as np

GLB_MAGIC = b"glTF"
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942
COMPONENT = {5120: "i1", 5121: "u1", 5122: "<i2", 5123: "<u2", 5125: "<u4", 5126: "<f4"}
NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}


class GltfOptions(PluginOptions):
    transform_tolerance: float = Field(1e-6, ge=0, description="Absolute tolerance for transform components.")
    geometry_tolerance: float = Field(0.0, ge=0, description="Max vertex displacement treated as unchanged.")
    material_tolerance: float = Field(1e-6, ge=0, description="Absolute tolerance for numeric material factors.")
    detect_renames: bool = Field(True, description="Report unique geometry+transform matches as renames.")
    max_json_mb: float = Field(64, gt=0, description="Larger glTF JSON is rejected.")
    preview: bool = Field(True, description="Render a wireframe preview (front/top) for reports.")
    max_preview_triangles: int = Field(50_000, ge=0, description="Triangles drawn per model in the wireframe preview.")


@dataclass
class Primitive:
    vertices: int
    faces: int
    mode: int
    attributes: list[str]
    material: str | None
    geometry_hash: str | None
    bbox: list[list[float]] | None
    positions: Any = None
    indices: Any = None


@dataclass
class Mesh:
    name: str
    primitives: list[Primitive]

    @property
    def geometry_hash(self) -> str | None:
        hs = [p.geometry_hash for p in self.primitives]
        return None if None in hs else hashlib.sha256("".join(hs).encode()).hexdigest()  # type: ignore[arg-type]


@dataclass
class Node:
    name: str
    index: int
    mesh: str | None
    local: list[float]  # column-major 4x4, as in glTF
    trs: dict[str, Any]
    children: list[str] = field(default_factory=list)
    world: Any = None


@dataclass
class Model:
    asset: dict[str, Any]
    nodes: dict[str, Node]
    meshes: dict[str, Mesh]
    materials: dict[str, dict[str, Any]]
    images: dict[str, dict[str, Any]]
    counts: dict[str, int]
    extensions_used: list[str]
    warnings: list[str]
    bbox: list[list[float]] | None = None


# ----------------------------------------------------------------------------- parsing
def _read_container(file: ArtifactFile, max_json: int) -> tuple[dict[str, Any], bytes | None]:
    with file.open() as fh:
        head = fh.read(12)
        if head[:4] != GLB_MAGIC:
            raw = head + fh.read(max_json + 1)
            if len(raw) > max_json:
                raise ResourceLimitError(f"{file.display_name}: glTF JSON exceeds {max_json:,} bytes")
            try:
                return json.loads(raw.decode("utf-8-sig")), None
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise PluginInputError(f"{file.display_name} is neither GLB nor valid glTF JSON: {exc}") from exc
        if len(head) < 12:
            raise PluginInputError(f"{file.display_name}: truncated GLB header")
        _, version, length = struct.unpack("<4sII", head)
        if version != 2:
            raise PluginInputError(f"{file.display_name}: unsupported GLB version {version} (only 2 is supported)")
        if length > file.size:
            raise PluginInputError(
                f"{file.display_name}: GLB header declares {length:,} bytes but file has {file.size:,}"
            )
        doc: dict[str, Any] | None = None
        binary: bytes | None = None
        pos = 12
        while pos + 8 <= length:
            clen, ctype = struct.unpack("<II", fh.read(8))
            pos += 8
            if pos + clen > length:
                raise PluginInputError(f"{file.display_name}: GLB chunk overruns the file")
            if ctype == CHUNK_JSON:
                if clen > max_json:
                    raise ResourceLimitError(f"{file.display_name}: glTF JSON chunk exceeds {max_json:,} bytes")
                try:
                    doc = json.loads(fh.read(clen).decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise PluginInputError(f"{file.display_name}: invalid JSON chunk: {exc}") from exc
            elif ctype == CHUNK_BIN and binary is None:
                binary = fh.read(clen)
            else:
                fh.seek(clen, 1)
            pos += clen
        if doc is None:
            raise PluginInputError(f"{file.display_name}: GLB has no JSON chunk")
        return doc, binary


def _safe_sibling(file: ArtifactFile, uri: str) -> Path | None:
    rel = PurePosixPath(unquote(uri))
    if rel.is_absolute() or ".." in rel.parts or ":" in uri.split("/", maxsplit=1)[0]:
        return None
    base = Path(file.path).resolve().parent
    p = (base / Path(*rel.parts)).resolve()
    return p if p.is_relative_to(base) and p.is_file() else None


def _uri_bytes(file: ArtifactFile, uri: str, warnings: list[str]) -> bytes | None:
    if uri.startswith("data:"):
        try:
            return base64.b64decode(uri.split(",", 1)[1], validate=False)
        except (IndexError, ValueError):
            warnings.append("A data: URI could not be decoded")
            return None
    p = _safe_sibling(file, uri)
    if p is None:
        warnings.append(
            f"External resource '{uri[:80]}' is not available next to the model (or is not allowed); "
            "its contents were not compared"
        )
        return None
    return p.read_bytes()


def _matrix(node: dict[str, Any]) -> tuple[list[float], dict[str, Any]]:
    import numpy as np

    if "matrix" in node:
        m = [float(x) for x in node["matrix"]]
        return m, {"matrix": m}
    t = [float(x) for x in node.get("translation", [0, 0, 0])]
    r = [float(x) for x in node.get("rotation", [0, 0, 0, 1])]
    s = [float(x) for x in node.get("scale", [1, 1, 1])]
    x, y, z, w = r
    rot = np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )
    mat = np.eye(4)
    mat[:3, :3] = rot * np.array(s)
    mat[:3, 3] = t
    return mat.T.flatten().tolist(), {"translation": t, "rotation": r, "scale": s}


def _names(items: list[dict[str, Any]], kind: str, warnings: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    unnamed = 0
    for i, it in enumerate(items):
        name = it.get("name")
        if not isinstance(name, str) or not name:
            name = f"{kind}#{i}"
            unnamed += 1
        if name in seen:
            seen[name] += 1
            name = f"{name}#{seen[name]}"
        else:
            seen[name] = 0
        out.append(name)
    if unnamed:
        warnings.append(f"{unnamed} unnamed {kind}(s) were identified by index")
    return out


def parse_model(file: ArtifactFile, opts: GltfOptions, keep_geometry: bool = True) -> Model:
    import numpy as np

    doc, glb_bin = _read_container(file, int(opts.max_json_mb * 1024 * 1024))
    if not isinstance(doc, dict) or str(doc.get("asset", {}).get("version", "")).split(".")[0] != "2":
        raise PluginInputError(f"{file.display_name}: not a glTF 2.0 asset (asset.version missing or not 2.x)")
    warnings: list[str] = []
    buffers: list[bytes | None] = []
    for i, b in enumerate(doc.get("buffers", [])):
        if "uri" in b:
            buffers.append(_uri_bytes(file, b["uri"], warnings))
        else:
            buffers.append(glb_bin if i == 0 else None)
    views = doc.get("bufferViews", [])
    accessors = doc.get("accessors", [])

    def view_bytes(vi: int) -> bytes | None:
        v = views[vi]
        buf = buffers[v.get("buffer", 0)] if v.get("buffer", 0) < len(buffers) else None
        if buf is None:
            return None
        off, ln = v.get("byteOffset", 0), v["byteLength"]
        if off + ln > len(buf):
            raise PluginInputError(f"{file.display_name}: bufferView {vi} exceeds its buffer")
        return bytes(buf[off : off + ln])

    def read_accessor(ai: int) -> np.ndarray | None:
        acc = accessors[ai]
        if "sparse" in acc:
            warnings.append(f"Sparse accessor {ai} was not decoded")
            return None
        if "bufferView" not in acc:
            return None
        data = view_bytes(acc["bufferView"])
        if data is None:
            return None
        dt = np.dtype(COMPONENT[acc["componentType"]])
        nc = NCOMP[acc["type"]]
        count = acc["count"]
        stride = views[acc["bufferView"]].get("byteStride") or dt.itemsize * nc
        off = acc.get("byteOffset", 0)
        need = off + stride * (count - 1) + dt.itemsize * nc if count else 0
        if need > len(data):
            raise PluginInputError(f"{file.display_name}: accessor {ai} reads past its bufferView")
        arr = np.ndarray((count, nc), dtype=dt, buffer=data, offset=off, strides=(stride, dt.itemsize))
        return np.ascontiguousarray(arr)

    mat_names = _names(doc.get("materials", []), "material", warnings)
    mesh_names = _names(doc.get("meshes", []), "mesh", warnings)
    node_names = _names(doc.get("nodes", []), "node", warnings)
    image_names = _names(doc.get("images", []), "image", warnings)

    meshes: dict[str, Mesh] = {}
    for mi, m in enumerate(doc.get("meshes", [])):
        prims = []
        for p in m.get("primitives", []):
            attrs = p.get("attributes", {})
            pos = read_accessor(attrs["POSITION"]) if "POSITION" in attrs else None
            idx = read_accessor(p["indices"]) if "indices" in p else None
            nverts = accessors[attrs["POSITION"]]["count"] if "POSITION" in attrs else 0
            mode = p.get("mode", 4)
            elems = accessors[p["indices"]]["count"] if "indices" in p else nverts
            faces = elems // 3 if mode == 4 else (max(0, elems - 2) if mode in (5, 6) else 0)
            ghash = None
            bbox = None
            if pos is not None and (idx is not None or "indices" not in p):
                h = hashlib.sha256(pos.astype("<f4").tobytes())
                if idx is not None:
                    h.update(idx.astype("<u4").tobytes())
                h.update(str(mode).encode())
                ghash = h.hexdigest()
            if pos is not None and len(pos):
                bbox = [
                    pos.min(axis=0).astype(float).round(6).tolist(),
                    pos.max(axis=0).astype(float).round(6).tolist(),
                ]
            elif "POSITION" in attrs and "min" in accessors[attrs["POSITION"]]:
                acc = accessors[attrs["POSITION"]]
                bbox = [acc["min"], acc["max"]]
            mat = (
                mat_names[p["material"]]
                if isinstance(p.get("material"), int) and p["material"] < len(mat_names)
                else None
            )
            prims.append(
                Primitive(
                    nverts,
                    faces,
                    mode,
                    sorted(attrs),
                    mat,
                    ghash,
                    bbox,
                    pos if keep_geometry else None,
                    idx if keep_geometry else None,
                )
            )
        meshes[mesh_names[mi]] = Mesh(mesh_names[mi], prims)

    nodes: dict[str, Node] = {}
    raw_nodes = doc.get("nodes", [])
    for ni, n in enumerate(raw_nodes):
        local, trs = _matrix(n)
        mesh = mesh_names[n["mesh"]] if isinstance(n.get("mesh"), int) and n["mesh"] < len(mesh_names) else None
        nodes[node_names[ni]] = Node(
            node_names[ni], ni, mesh, local, trs, [node_names[c] for c in n.get("children", []) if c < len(node_names)]
        )
    # world transforms from the default scene (or all roots)
    scenes = doc.get("scenes", [])
    scene = scenes[doc.get("scene", 0)] if scenes and doc.get("scene", 0) < len(scenes) else None
    roots = (
        scene.get("nodes", [])
        if scene
        else [i for i in range(len(raw_nodes)) if not any(i in rn.get("children", []) for rn in raw_nodes)]
    )
    stack = [(r, np.eye(4)) for r in roots if r < len(node_names)]
    visited: set[int] = set()
    while stack:
        ni, parent = stack.pop()
        if ni in visited:
            warnings.append("Node hierarchy contains a cycle; it was broken")
            continue
        visited.add(ni)
        node = nodes[node_names[ni]]
        node.world = parent @ np.array(node.local).reshape(4, 4).T
        stack.extend((c, node.world) for c in raw_nodes[ni].get("children", []) if c < len(node_names))

    lo = np.full(3, np.inf)
    hi = np.full(3, -np.inf)
    for node in nodes.values():
        if node.mesh is None or node.world is None:
            continue
        for p in meshes[node.mesh].primitives:
            if p.bbox is None:
                continue
            (x0, y0, z0), (x1, y1, z1) = p.bbox
            corners = np.array([[x, y, z, 1] for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)])
            wc = (node.world @ corners.T).T[:, :3]
            lo, hi = np.minimum(lo, wc.min(axis=0)), np.maximum(hi, wc.max(axis=0))

    materials: dict[str, dict[str, Any]] = {}
    textures = doc.get("textures", [])
    for i, m in enumerate(doc.get("materials", [])):
        pbr = m.get("pbrMetallicRoughness", {})

        def tex(ref: Any) -> str | None:
            if not isinstance(ref, dict) or ref.get("index", -1) >= len(textures):
                return None
            src = textures[ref["index"]].get("source")
            return image_names[src] if isinstance(src, int) and src < len(image_names) else f"texture#{ref['index']}"

        materials[mat_names[i]] = {
            "baseColorFactor": pbr.get("baseColorFactor", [1, 1, 1, 1]),
            "metallicFactor": pbr.get("metallicFactor", 1.0),
            "roughnessFactor": pbr.get("roughnessFactor", 1.0),
            "emissiveFactor": m.get("emissiveFactor", [0, 0, 0]),
            "alphaMode": m.get("alphaMode", "OPAQUE"),
            "alphaCutoff": m.get("alphaCutoff", 0.5),
            "doubleSided": m.get("doubleSided", False),
            "baseColorTexture": tex(pbr.get("baseColorTexture")),
            "metallicRoughnessTexture": tex(pbr.get("metallicRoughnessTexture")),
            "normalTexture": tex(m.get("normalTexture")),
            "occlusionTexture": tex(m.get("occlusionTexture")),
            "emissiveTexture": tex(m.get("emissiveTexture")),
            "extensions": sorted(m.get("extensions", {})),
        }
    images: dict[str, dict[str, Any]] = {}
    for i, im in enumerate(doc.get("images", [])):
        data = None
        if "bufferView" in im and im["bufferView"] < len(views):
            data = view_bytes(im["bufferView"])
        elif "uri" in im:
            data = _uri_bytes(file, im["uri"], warnings)
        info: dict[str, Any] = {
            "mimeType": im.get("mimeType"),
            "sha256": hashlib.sha256(data).hexdigest()[:16] if data is not None else None,
            "bytes": len(data) if data is not None else None,
        }
        if data is not None:
            try:
                import io

                from PIL import Image

                with Image.open(io.BytesIO(data)) as pil:
                    info["size"] = list(pil.size)
            except Exception:
                info["size"] = None
        images[image_names[i]] = info

    counts = {
        k: len(doc.get(k, []))
        for k in ("scenes", "nodes", "meshes", "materials", "textures", "images", "animations", "skins", "cameras")
    }
    lights = doc.get("extensions", {}).get("KHR_lights_punctual", {}).get("lights", [])
    counts["lights"] = len(lights)
    model = Model(
        doc.get("asset", {}), nodes, meshes, materials, images, counts, sorted(doc.get("extensionsUsed", [])), warnings
    )
    if np.isfinite(lo).all():
        model.bbox = [lo.round(6).tolist(), hi.round(6).tolist()]
    return model


def _close(a: list[float], b: list[float], tol: float) -> bool:
    return len(a) == len(b) and all(abs(x - y) <= tol for x, y in zip(a, b, strict=True))


def _values_equal(a: Any, b: Any, tol: float) -> bool:
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_values_equal(x, y, tol) for x, y in zip(a, b, strict=True))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return abs(float(a) - float(b)) <= tol
    return bool(a == b)


class GltfPlugin(ArtifactPlugin):
    name = "gltf"
    version = "1.0.0"
    display_name = "glTF / GLB 3D models"
    description = "Scene-graph, transform, mesh-geometry, material, texture and bounds comparison of glTF 2.0."
    format_id = "gltf"
    format_name = "glTF 2.0"
    supported_extensions = (".glb", ".gltf")
    mime_types = ("model/gltf-binary", "model/gltf+json")
    signatures = (GLB_MAGIC,)
    capabilities = (
        "scene-graph",
        "transforms",
        "geometry-hash",
        "vertex-displacement",
        "materials",
        "textures",
        "bounding-box",
        "rename-detection",
        "wireframe-preview",
    )
    limitations = (
        "Only glTF 2.0 is supported; Draco/meshopt-compressed and sparse accessors are not decoded.",
        "External .bin/texture files are compared only when they sit next to the model file.",
        "Animations, skins and cameras are compared by count only.",
    )
    requires = ("numpy",)
    install_hint = 'pip install "artifactdiff[gltf]"'
    Options = GltfOptions

    def inspect(self, file: ArtifactFile) -> dict[str, Any]:
        m = parse_model(file, GltfOptions(), keep_geometry=False)
        return {
            "generator": m.asset.get("generator"),
            "version": m.asset.get("version"),
            **m.counts,
            "vertices": sum(p.vertices for me in m.meshes.values() for p in me.primitives),
            "faces": sum(p.faces for me in m.meshes.values() for p in me.primitives),
            "bounding_box": m.bbox,
            "extensions_used": m.extensions_used,
        }

    def compare(self, old: ArtifactFile, new: ArtifactFile, options: PluginOptions, ctx: ComparisonContext) -> None:
        assert isinstance(options, GltfOptions)
        a, b = parse_model(old, options), parse_model(new, options)
        for side, m in (("old", a), ("new", b)):
            for w in m.warnings:
                ctx.warn("gltf-" + side, f"{side}: {w}")
        if any(
            "compression" in e or "draco" in e.lower() or "meshopt" in e for e in a.extensions_used + b.extensions_used
        ):
            ctx.warn("compressed-geometry", "Compressed geometry extensions are used; compressed data was not decoded")
        ctx.check()
        self._meta(ctx, a, b)
        self._meshes(ctx, a, b, options)
        self._nodes(ctx, a, b, options)
        self._materials(ctx, a, b, options)
        if a.bbox != b.bbox:
            ctx.add_change(
                ChangeKind.MODIFIED,
                ChangeCategory.GEOMETRY,
                "scene/bounding_box",
                "Scene bounding box changed",
                old=a.bbox,
                new=b.bbox,
                severity=ChangeSeverity.LOW,
                evidence="World-space AABB of all mesh nodes in the default scene",
            )
        ctx.metric("old_bounding_box", a.bbox)
        ctx.metric("new_bounding_box", b.bbox)
        for side, m in (("old", a), ("new", b)):
            ctx.metric(f"{side}_vertices", sum(p.vertices for me in m.meshes.values() for p in me.primitives))
            ctx.metric(f"{side}_faces", sum(p.faces for me in m.meshes.values() for p in me.primitives))
        if options.preview and ctx.generate_assets:
            self._preview(ctx, a, b, options)

    # ------------------------------------------------------------------ sections
    def _meta(self, ctx: ComparisonContext, a: Model, b: Model) -> None:
        for key in ("generator", "version", "copyright"):
            if a.asset.get(key) != b.asset.get(key):
                ctx.add_change(
                    ChangeKind.MODIFIED,
                    ChangeCategory.METADATA,
                    f"asset:{key}",
                    f"Asset {key} changed",
                    old=a.asset.get(key),
                    new=b.asset.get(key),
                    severity=ChangeSeverity.INFO,
                    evidence="glTF asset object",
                )
        for key in sorted(set(a.counts) | set(b.counts)):
            if key in ("nodes", "meshes", "materials", "images", "textures"):
                continue  # reported element by element
            if a.counts.get(key) != b.counts.get(key):
                ctx.add_change(
                    ChangeKind.MODIFIED,
                    ChangeCategory.STRUCTURE,
                    f"count:{key}",
                    f"Number of {key} changed",
                    old=a.counts.get(key),
                    new=b.counts.get(key),
                    severity=ChangeSeverity.LOW,
                    evidence=f"Length of the glTF '{key}' array",
                )
        if a.extensions_used != b.extensions_used:
            ctx.add_change(
                ChangeKind.MODIFIED,
                ChangeCategory.METADATA,
                "extensionsUsed",
                "Extensions used changed",
                old=a.extensions_used,
                new=b.extensions_used,
                severity=ChangeSeverity.LOW,
                evidence="glTF extensionsUsed",
            )

    def _meshes(self, ctx: ComparisonContext, a: Model, b: Model, opts: GltfOptions) -> None:
        import numpy as np

        for name, ma in a.meshes.items():
            mb = b.meshes.get(name)
            path = f"mesh:{name}"
            if mb is None:
                ctx.add_change(
                    ChangeKind.REMOVED,
                    ChangeCategory.STRUCTURE,
                    path,
                    f"Mesh '{name}' removed",
                    old={"primitives": len(ma.primitives)},
                    severity=ChangeSeverity.MEDIUM,
                    evidence="Mesh matched by name",
                )
                continue
            if len(ma.primitives) != len(mb.primitives):
                ctx.add_change(
                    ChangeKind.MODIFIED,
                    ChangeCategory.STRUCTURE,
                    f"{path}/primitives",
                    f"Primitive count of '{name}' changed",
                    old=len(ma.primitives),
                    new=len(mb.primitives),
                    severity=ChangeSeverity.MEDIUM,
                    evidence="mesh.primitives length",
                )
            for i, (pa, pb) in enumerate(zip(ma.primitives, mb.primitives, strict=False)):
                pp = f"{path}/primitive[{i}]"
                if pa.vertices != pb.vertices:
                    ctx.add_change(
                        ChangeKind.MODIFIED,
                        ChangeCategory.GEOMETRY,
                        f"{pp}/vertices",
                        f"Vertex count of '{name}' changed by {pb.vertices - pa.vertices:+,}",
                        old=pa.vertices,
                        new=pb.vertices,
                        severity=ChangeSeverity.MEDIUM,
                        evidence="POSITION accessor count",
                    )
                if pa.faces != pb.faces:
                    ctx.add_change(
                        ChangeKind.MODIFIED,
                        ChangeCategory.GEOMETRY,
                        f"{pp}/faces",
                        f"Face count of '{name}' changed by {pb.faces - pa.faces:+,}",
                        old=pa.faces,
                        new=pb.faces,
                        severity=ChangeSeverity.MEDIUM,
                        evidence="Index count / 3 (triangles) or strip/fan formula",
                    )
                if pa.attributes != pb.attributes:
                    ctx.add_change(
                        ChangeKind.MODIFIED,
                        ChangeCategory.STRUCTURE,
                        f"{pp}/attributes",
                        f"Vertex attributes of '{name}' changed",
                        old=pa.attributes,
                        new=pb.attributes,
                        severity=ChangeSeverity.LOW,
                        evidence="primitive.attributes keys",
                    )
                if pa.material != pb.material:
                    ctx.add_change(
                        ChangeKind.MODIFIED,
                        ChangeCategory.MATERIAL,
                        f"{pp}/material",
                        f"Material assigned to '{name}' changed",
                        old=pa.material,
                        new=pb.material,
                        severity=ChangeSeverity.LOW,
                        evidence="primitive.material reference (by name)",
                    )
                if pa.geometry_hash is None or pb.geometry_hash is None:
                    ctx.warn("geometry-unavailable", f"Vertex data of '{name}' unavailable; geometry not compared", pp)
                    continue
                if pa.geometry_hash == pb.geometry_hash:
                    continue
                details: dict[str, Any] = {"old_hash": pa.geometry_hash[:16], "new_hash": pb.geometry_hash[:16]}
                desc = f"Geometry of '{name}' modified"
                if pa.vertices == pb.vertices and pa.positions is not None and pb.positions is not None:
                    disp = np.linalg.norm(pa.positions.astype(np.float64) - pb.positions.astype(np.float64), axis=1)
                    mx = float(disp.max()) if disp.size else 0.0
                    same_idx = (pa.indices is None and pb.indices is None) or (
                        pa.indices is not None and pb.indices is not None and np.array_equal(pa.indices, pb.indices)
                    )
                    if mx <= opts.geometry_tolerance and same_idx:
                        continue
                    details |= {
                        "max_vertex_displacement": round(mx, 6),
                        "moved_vertices": int((disp > opts.geometry_tolerance).sum()),
                        "topology_changed": not same_idx,
                    }
                    desc += (
                        f": {details['moved_vertices']:,} vertices moved (max {mx:.4g})"
                        if same_idx
                        else ": triangle indices changed"
                    )
                    if pa.faces == pb.faces:
                        desc += " — counts unchanged"
                ctx.add_change(
                    ChangeKind.MODIFIED,
                    ChangeCategory.GEOMETRY,
                    f"{pp}/geometry",
                    desc,
                    old=pa.bbox,
                    new=pb.bbox,
                    severity=ChangeSeverity.MEDIUM,
                    evidence="SHA-256 of POSITION and index data differs",
                    details=details,
                )
        for name, mb in b.meshes.items():
            if name not in a.meshes:
                ctx.add_change(
                    ChangeKind.ADDED,
                    ChangeCategory.STRUCTURE,
                    f"mesh:{name}",
                    f"Mesh '{name}' added",
                    new={"primitives": len(mb.primitives), "vertices": sum(p.vertices for p in mb.primitives)},
                    severity=ChangeSeverity.LOW,
                    evidence="Mesh matched by name",
                )

    def _nodes(self, ctx: ComparisonContext, a: Model, b: Model, opts: GltfOptions) -> None:
        removed = [n for n in a.nodes if n not in b.nodes]
        added = [n for n in b.nodes if n not in a.nodes]
        renames: dict[str, str] = {}
        if opts.detect_renames and removed and added:

            def sig(m: Model, n: Node) -> tuple[Any, ...]:
                g = m.meshes[n.mesh].geometry_hash if n.mesh else None
                return (g, tuple(round(x, 6) for x in n.local))

            sig_b: dict[tuple[Any, ...], list[str]] = {}
            for n in added:
                sig_b.setdefault(sig(b, b.nodes[n]), []).append(n)
            sig_a: dict[tuple[Any, ...], list[str]] = {}
            for n in removed:
                sig_a.setdefault(sig(a, a.nodes[n]), []).append(n)
            for s, olds in sig_a.items():
                news = sig_b.get(s, [])
                if len(olds) == 1 and len(news) == 1 and s[0] is not None:
                    renames[olds[0]] = news[0]
        for old_name, new_name in renames.items():
            ctx.add_change(
                ChangeKind.RENAMED,
                ChangeCategory.STRUCTURE,
                f"node:{new_name}",
                f"Node '{old_name}' renamed to '{new_name}'",
                old=old_name,
                new=new_name,
                confidence=0.9,
                severity=ChangeSeverity.LOW,
                evidence="Exactly one removed/added node pair with identical mesh geometry hash and local transform",
            )
        for n in removed:
            if n not in renames:
                ctx.add_change(
                    ChangeKind.REMOVED,
                    ChangeCategory.STRUCTURE,
                    f"node:{n}",
                    f"Object '{n}' removed",
                    old={"mesh": a.nodes[n].mesh},
                    severity=ChangeSeverity.MEDIUM,
                    evidence="Node matched by name",
                )
        renamed_new = set(renames.values())
        for n in added:
            if n not in renamed_new:
                ctx.add_change(
                    ChangeKind.ADDED,
                    ChangeCategory.STRUCTURE,
                    f"node:{n}",
                    f"Object '{n}' added",
                    new={"mesh": b.nodes[n].mesh},
                    severity=ChangeSeverity.LOW,
                    evidence="Node matched by name",
                )
        for name, na in a.nodes.items():
            nb = b.nodes.get(name)
            if nb is None:
                continue
            if not _close(na.local, nb.local, opts.transform_tolerance):
                ctx.add_change(
                    ChangeKind.MODIFIED,
                    ChangeCategory.GEOMETRY,
                    f"node:{name}/transform",
                    f"Transform of '{name}' changed",
                    old=na.trs,
                    new=nb.trs,
                    severity=ChangeSeverity.LOW,
                    evidence=f"Local 4x4 matrices differ by more than {opts.transform_tolerance:g}",
                )
            if na.mesh != nb.mesh:
                ctx.add_change(
                    ChangeKind.MODIFIED,
                    ChangeCategory.STRUCTURE,
                    f"node:{name}/mesh",
                    f"Mesh referenced by '{name}' changed",
                    old=na.mesh,
                    new=nb.mesh,
                    severity=ChangeSeverity.MEDIUM,
                    evidence="node.mesh reference (by name)",
                )
            if na.children != nb.children:
                ctx.add_change(
                    ChangeKind.MODIFIED,
                    ChangeCategory.STRUCTURE,
                    f"node:{name}/children",
                    f"Children of '{name}' changed",
                    old=na.children,
                    new=nb.children,
                    severity=ChangeSeverity.LOW,
                    evidence="node.children (by name)",
                )

    def _materials(self, ctx: ComparisonContext, a: Model, b: Model, opts: GltfOptions) -> None:
        for name, ma in a.materials.items():
            mb = b.materials.get(name)
            if mb is None:
                ctx.add_change(
                    ChangeKind.REMOVED,
                    ChangeCategory.MATERIAL,
                    f"material:{name}",
                    f"Material '{name}' removed",
                    severity=ChangeSeverity.LOW,
                    evidence="Material matched by name",
                )
                continue
            for key in ma:
                if not _values_equal(ma[key], mb.get(key), opts.material_tolerance):
                    ctx.add_change(
                        ChangeKind.MODIFIED,
                        ChangeCategory.MATERIAL,
                        f"material:{name}/{key}",
                        f"Material '{name}' {key} changed",
                        old=ma[key],
                        new=mb.get(key),
                        severity=ChangeSeverity.LOW,
                        evidence="glTF material properties",
                    )
        for name in b.materials:
            if name not in a.materials:
                ctx.add_change(
                    ChangeKind.ADDED,
                    ChangeCategory.MATERIAL,
                    f"material:{name}",
                    f"Material '{name}' added",
                    severity=ChangeSeverity.LOW,
                    evidence="Material matched by name",
                )
        for name, ia in a.images.items():
            ib = b.images.get(name)
            if ib is None:
                ctx.add_change(
                    ChangeKind.REMOVED,
                    ChangeCategory.MATERIAL,
                    f"texture:{name}",
                    f"Texture image '{name}' removed",
                    old=ia,
                    severity=ChangeSeverity.LOW,
                    evidence="Image matched by name",
                )
            elif ia != ib:
                ctx.add_change(
                    ChangeKind.MODIFIED,
                    ChangeCategory.MATERIAL,
                    f"texture:{name}",
                    f"Texture image '{name}' changed",
                    old=ia,
                    new=ib,
                    severity=ChangeSeverity.LOW,
                    evidence="SHA-256, byte size, MIME type and pixel size of the embedded image",
                )
        for name, ib in b.images.items():
            if name not in a.images:
                ctx.add_change(
                    ChangeKind.ADDED,
                    ChangeCategory.MATERIAL,
                    f"texture:{name}",
                    f"Texture image '{name}' added",
                    new=ib,
                    severity=ChangeSeverity.LOW,
                    evidence="Image matched by name",
                )

    def _preview(self, ctx: ComparisonContext, a: Model, b: Model, opts: GltfOptions) -> None:
        import numpy as np
        from PIL import Image, ImageDraw

        def tris(m: Model) -> list[Any]:
            out = []
            for node in m.nodes.values():
                if node.mesh is None or node.world is None:
                    continue
                for p in m.meshes[node.mesh].primitives:
                    if p.positions is None or p.mode != 4:
                        continue
                    pos = np.c_[p.positions.astype(np.float64), np.ones(len(p.positions))] @ node.world.T
                    idx = p.indices.reshape(-1) if p.indices is not None else np.arange(len(pos))
                    out.append(pos[:, :3][idx[: len(idx) // 3 * 3].reshape(-1, 3)])
            return out

        ta, tb = tris(a), tris(b)
        allpts = [t.reshape(-1, 3) for t in ta + tb]
        if not allpts:
            return
        pts = np.concatenate(allpts)
        lo, hi = pts.min(axis=0), pts.max(axis=0)
        span = float(max((hi - lo).max(), 1e-9))
        size, pad = 360, 16
        img = Image.new("RGB", (size * 2 + pad, size), (13, 17, 23))
        draw = ImageDraw.Draw(img)
        views = ((0, 1, 0, "front (X/Y)"), (0, 2, size + pad, "top (X/Z)"))
        budget = opts.max_preview_triangles
        for group, color in ((ta, (122, 162, 247)), (tb, (255, 158, 59))):
            drawn = 0
            for t in group:
                for tri in t:
                    if drawn >= budget:
                        break
                    drawn += 1
                    for ax, ay, ox, _ in views:
                        xy = [
                            (
                                ox + 12 + (v[ax] - lo[ax]) / span * (size - 24),
                                size - 12 - (v[ay] - lo[ay]) / span * (size - 24),
                            )
                            for v in tri
                        ]
                        draw.line([*xy, xy[0]], fill=color, width=1)
        for _, _, ox, label in views:
            draw.text((ox + 6, 4), label, fill=(200, 200, 200))
        draw.text((6, size - 14), "blue = old, orange = new", fill=(200, 200, 200))
        data, w, h = png_bytes(img)
        ctx.add_asset(
            "wireframe", "Wireframe preview, orthographic front and top views", "image/png", data, width=w, height=h
        )
