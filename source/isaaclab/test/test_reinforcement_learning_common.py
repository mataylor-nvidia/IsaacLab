# Copyright (c) 2022-2026, The Isaac Lab Project Developers (https://github.com/isaac-sim/IsaacLab/blob/main/CONTRIBUTORS.md).
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause

"""Unit tests for shared reinforcement learning script utilities."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import gymnasium as gym
import pytest
import torch


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _load_rl_common_module() -> ModuleType:
    module_path = _repo_root() / "scripts" / "reinforcement_learning" / "common.py"
    spec = importlib.util.spec_from_file_location("isaaclab_test_reinforcement_learning_common", module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load reinforcement learning common module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_rl_common = _load_rl_common_module()
CaptureEnvSensors: Any = getattr(_rl_common, "CaptureEnvSensors")
add_common_train_args: Any = getattr(_rl_common, "add_common_train_args")
enable_cameras_for_video: Any = getattr(_rl_common, "enable_cameras_for_video")
wrap_sensor_capture: Any = getattr(_rl_common, "wrap_sensor_capture")


class _FakeEnv(gym.Env):
    """Minimal Gymnasium env exposing an IsaacLab-style scene sensor mapping."""

    def __init__(self, sensors: dict[str, Any] | None = None) -> None:
        self.scene = SimpleNamespace(sensors=sensors or {})
        self.closed = False

    def reset(self, **kwargs: Any) -> tuple[dict[str, torch.Tensor], dict[str, Any]]:
        return {"obs": torch.zeros(1)}, {}

    def step(self, action: Any) -> tuple[dict[str, torch.Tensor], float, bool, bool, dict[str, Any]]:
        return {"obs": torch.ones(1)}, 0.0, False, False, {}

    def close(self) -> None:
        self.closed = True


def _make_sensor(output: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(data=SimpleNamespace(output=output))


def _make_capture_wrapper(tmp_path: Path, **kwargs: Any) -> Any:
    defaults = {
        "env": _FakeEnv(),
        "output_dir": str(tmp_path),
        "frame_count": 1,
        "num_envs": 1,
        "interval": 1,
        "sensor_names": None,
        "data_types": None,
        "output_format": "file",
    }
    defaults.update(kwargs)
    return CaptureEnvSensors(**defaults)


def test_capture_env_sensors_saves_file_outputs_on_scheduled_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """File capture writes filtered image tensors during the active capture window."""
    rgb = torch.tensor(
        [
            [[[0, 127, 255, 9], [255, 0, 127, 9]]],
            [[[42, 42, 42, 9], [43, 43, 43, 9]]],
        ],
        dtype=torch.uint8,
    )
    sensors = {
        "front/camera": _make_sensor({"rgb": rgb, "distance_to_camera": torch.ones((2, 1, 2, 1))}),
        "side camera": _make_sensor({"rgb": rgb}),
        "not_image": _make_sensor({"rgb": torch.ones((1, 2, 3))}),
    }
    env = _FakeEnv(sensors)
    saved_images: list[torch.Tensor] = []
    saved_paths: list[Path] = []

    def fake_save_images_to_file(images: torch.Tensor, path: str) -> None:
        saved_images.append(images.clone())
        saved_paths.append(Path(path))

    monkeypatch.setattr(_rl_common, "save_images_to_file", fake_save_images_to_file)
    wrapper = _make_capture_wrapper(
        tmp_path,
        env=env,
        frame_count=2,
        num_envs=1,
        interval=3,
        sensor_names={"front/camera"},
        data_types={"rgb"},
    )

    wrapper.reset()
    wrapper.step(None)
    wrapper.step(None)
    wrapper.step(None)

    relative_paths = [path.relative_to(tmp_path).as_posix() for path in saved_paths]
    assert relative_paths == [
        "front_camera/rgb/step_00000000_frame_000000.png",
        "front_camera/rgb/step_00000001_frame_000001.png",
        "front_camera/rgb/step_00000003_frame_000002.png",
    ]
    assert all(image.shape == (1, 1, 2, 3) for image in saved_images)
    assert all(torch.allclose(image, rgb[:1, :, :, :3].float() / 255.0) for image in saved_images)


@pytest.mark.parametrize(
    ("data_type", "image_buffer", "expected"),
    [
        (
            "distance_to_camera",
            torch.tensor([[[[0.0], [2.0]], [[float("inf")], [float("nan")]]]]),
            torch.tensor([[[[0.0], [1.0]], [[0.0], [0.0]]]]),
        ),
        (
            "normals",
            torch.tensor([[[[-1.0, 0.0, 1.0], [1.0, -1.0, 0.0]]]]),
            torch.tensor([[[[0.0, 0.5, 1.0], [1.0, 0.0, 0.5]]]]),
        ),
        (
            "semantic_segmentation",
            torch.tensor([[[[-1.0, 1.0, 3.0]]]]),
            torch.tensor([[[[0.0, 0.5, 1.0]]]]),
        ),
    ],
)
def test_capture_env_sensors_normalizes_float_image_buffers(
    tmp_path: Path, data_type: str, image_buffer: torch.Tensor, expected: torch.Tensor
) -> None:
    """Float image-like buffers are normalized according to their sensor data type."""
    wrapper = _make_capture_wrapper(tmp_path)

    image_tensor = wrapper._to_image_tensor(image_buffer, data_type)

    assert image_tensor is not None
    assert torch.allclose(image_tensor, expected)


def test_capture_env_sensors_accepts_proxyarray_torch_buffers(tmp_path: Path) -> None:
    """ProxyArray-style buffers are read through their ``.torch`` accessor."""
    wrapper = _make_capture_wrapper(tmp_path, num_envs=2)
    image_buffer = SimpleNamespace(torch=torch.ones((3, 2, 2, 4), dtype=torch.float32))

    image_tensor = wrapper._to_image_tensor(image_buffer, "rgb")

    assert image_tensor is not None
    assert image_tensor.shape == (2, 2, 2, 3)
    assert image_tensor.is_contiguous()
    assert torch.allclose(image_tensor, torch.ones((2, 2, 2, 3)))


@pytest.mark.parametrize(
    "image_buffer",
    [
        torch.ones((1, 2, 3)),
        torch.ones((1, 2, 2, 0)),
        SimpleNamespace(torch="not a tensor"),
        object(),
    ],
)
def test_capture_env_sensors_rejects_non_image_buffers(tmp_path: Path, image_buffer: Any) -> None:
    """Non-image buffers are skipped instead of being written."""
    wrapper = _make_capture_wrapper(tmp_path)

    assert wrapper._to_image_tensor(image_buffer, "rgb") is None


def test_capture_env_sensors_rejects_unknown_output_format(tmp_path: Path) -> None:
    """Only tensorboard and file output formats are supported."""
    with pytest.raises(ValueError, match="Unsupported sensor capture output format"):
        _make_capture_wrapper(tmp_path, output_format="invalid")


def test_wrap_sensor_capture_uses_training_sensor_frame_directory(tmp_path: Path) -> None:
    """The train helper wraps the env with the configured sensor capture output directory."""
    env = _FakeEnv()
    args_cli = argparse.Namespace(
        capture_env_sensors=2,
        capture_env_sensors_length=5,
        capture_env_sensors_interval=7,
        capture_env_sensors_format="file",
    )

    wrapped_env = wrap_sensor_capture(env, str(tmp_path), args_cli)

    assert isinstance(wrapped_env, CaptureEnvSensors)
    assert Path(wrapped_env.output_dir) == tmp_path / "sensor_frames" / "train"
    assert wrapped_env.frame_count == 5
    assert wrapped_env.num_envs == 2
    assert wrapped_env.interval == 7
    assert wrapped_env.env is env


def test_wrap_sensor_capture_returns_env_when_disabled(tmp_path: Path) -> None:
    """The train helper leaves the env unwrapped when sensor capture is disabled."""
    env = _FakeEnv()
    args_cli = argparse.Namespace(capture_env_sensors=0)

    assert wrap_sensor_capture(env, str(tmp_path), args_cli) is env


def test_common_train_args_include_sensor_capture_options() -> None:
    """Common train parsers expose sensor capture CLI arguments."""
    parser = argparse.ArgumentParser()
    add_common_train_args(parser, agent_default=None, agent_help="", include_agent=False)

    args_cli = parser.parse_args(
        [
            "--capture_env_sensors",
            "3",
            "--capture_env_sensors_length",
            "4",
            "--capture_env_sensors_interval",
            "5",
            "--capture_env_sensors_format",
            "file",
        ]
    )

    assert args_cli.capture_env_sensors == 3
    assert args_cli.capture_env_sensors_length == 4
    assert args_cli.capture_env_sensors_interval == 5
    assert args_cli.capture_env_sensors_format == "file"


def test_enable_cameras_for_video_enables_cameras_for_sensor_capture() -> None:
    """Sensor capture requires camera rendering even when normal video capture is disabled."""
    args_cli = argparse.Namespace(video=False, capture_env_sensors=1, enable_cameras=False)

    enable_cameras_for_video(args_cli)

    assert args_cli.enable_cameras
