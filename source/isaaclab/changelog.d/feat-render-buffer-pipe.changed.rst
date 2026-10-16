* **Breaking:** Replaced renderer-owned PPISP with camera processors consuming ``rgb_hdr``.
  Replace ``CameraCfg.isp_cfg`` with ``PpispProcessorCfg`` in ``CameraCfg.pipeline``.
  Set ``CameraCfg.exposure=CameraExposureCfg()`` explicitly for neutral RTX HDR before PPISP.
