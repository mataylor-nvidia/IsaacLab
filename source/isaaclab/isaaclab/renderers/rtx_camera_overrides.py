# Copyright (c) 2022-2026, The Isaac Lab Project Developers (https://github.com/isaac-sim/IsaacLab/blob/main/CONTRIBUTORS.md).
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause

"""Internal RTX camera exposure overrides for scene-linear sensor inputs.

RTX has no native pre-exposure radiance output, so ``rgb_radiance`` is derived by authoring neutral
exposure on the camera prim, which affects every output rendered from it. This is a temporary
workaround shared by the Isaac RTX and OVRTX renderers, not public API. Remove it once RTX provides
a native radiance output (NVBug 6858736).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .camera_render_spec import CameraRenderSpec

# Both RTX backends consume these schemas without requiring the schema plugin at authoring time.
_CAMERA_EXPOSURE_SCHEMAS = ("OmniRtxCameraAutoExposureAPI_1", "OmniRtxCameraExposureAPI_1")
_NEUTRAL_CAMERA_EXPOSURE: tuple[tuple[str, str, Any], ...] = (
    ("exposure", "Float", 0.0),
    ("exposure:fStop", "Float", 1.0),
    ("exposure:iso", "Float", 0.0),
    ("exposure:responsivity", "Float", 1.0),
    ("exposure:time", "Float", 1.0),
    ("omni:rtx:autoExposure:enabled", "Bool", False),
)


def _apply_rtx_exposure_overrides(stage: Any, spec: CameraRenderSpec) -> None:
    """Neutralize RTX exposure for radiance or ISP inputs; preserve standalone HDR exposure.

    Existing API schemas are preserved. Schema metadata is authored directly so the RTX schema
    plugin does not need to be loaded yet.

    Args:
        stage: USD stage containing the camera prims.
        spec: Camera configuration and resolved camera prim paths.
    """
    if spec.cfg.isp_cfg is None and "rgb_radiance" not in spec.cfg.data_types:
        return

    from pxr import Sdf

    value_types = {"Bool": Sdf.ValueTypeNames.Bool, "Float": Sdf.ValueTypeNames.Float}
    for prim_path in spec.camera_prim_paths:
        prim = stage.GetPrimAtPath(prim_path)
        if not prim or not prim.IsValid():
            continue
        current = prim.GetMetadata("apiSchemas") or Sdf.TokenListOp()
        existing = list(current.prependedItems) + list(current.explicitItems) + list(current.appendedItems)
        missing = [schema for schema in _CAMERA_EXPOSURE_SCHEMAS if schema not in existing]
        if missing:
            prim.SetMetadata("apiSchemas", Sdf.TokenListOp.Create(prependedItems=[*missing, *existing]))
        for attr_name, sdf_type_name, value in _NEUTRAL_CAMERA_EXPOSURE:
            attr = prim.GetAttribute(attr_name)
            if not attr:
                attr = prim.CreateAttribute(attr_name, value_types[sdf_type_name], custom=False)
            attr.Set(value)
