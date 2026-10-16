# Isaac Lab PPISP

This extension provides PPISP (Physically Plausible Image Signal Processing) as
a `DictTensorProcessor` for camera pipelines and directly supplied tensors.

`PpispProcessor` reads scene-linear `rgb_hdr` from a float32 `ProxyArray` with
shape `(N, H, W, 3)` and writes uint8 `rgb`. For camera processing, configure
`CameraCfg.pipeline=[PpispProcessorCfg(params={"cfg": ppisp_cfg})]`. The camera requests
required HDR internally; only the requested public outputs are published. Set
`CameraCfg.exposure=CameraExposureCfg()` (from `isaaclab.sensors.camera`) before initialization
for neutral RTX HDR input. Output selection does not alter exposure.
Set PPISP exposure in stops through `PpispCfg.inputs["exposureOffset"]`.

Resolve authored settings explicitly before constructing the pipeline:

```python
from isaaclab_ppisp import PpispProcessorCfg, ppisp_cfg_from_usd_camera

ppisp_cfg = ppisp_cfg_from_usd_camera(source_camera_prim)
processor_cfg = PpispProcessorCfg(params={"cfg": ppisp_cfg})
```

For RGBA, use `outputs=["rgba"]` and `params={"cfg": ppisp_cfg, "channels": 4}`.
Inputs must contain scene-linear color before camera exposure and response, including when
supplied by another processor; float32 display-encoded RGB is not radiance.

Each processor owns its controller scratch and output buffers. Optional
`params["controller_weights"]=PpispControllerWeights(device_weights)` can supply
shared, read-only device weights; configuration copies preserve this resource's identity. The
caller owns upload synchronization, and mutable controller workspace stays private.
The controller requires CUDA; static PPISP also runs on CPU. `PpispPipeline` remains
available for direct numerical use with explicitly supplied Warp buffers.

For direct use, construct `processor = PpispProcessor(processor_cfg, {"rgb_hdr": hdr_tensor})`,
then call `processor({"rgb_hdr": hdr_tensor})` to obtain `{"rgb": color_tensor}`. Copy results
that must survive another call, and call `processor.close()` when finished.
