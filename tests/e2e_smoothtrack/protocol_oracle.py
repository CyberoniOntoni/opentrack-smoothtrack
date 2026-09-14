"""Authoritative Protocol Oracle & Reference Implementation for SmoothTrack Telemetry.

Derived from:
- ORIGINAL_REQUEST.md (R1: 48-byte double[6] pose stream parser, IEEE 754 little-endian)
- PROJECT.md (Interface Contracts, Frame Ingestion, std::fpclassify validation, Axis Offsets)
- tracker-smoothtrack/ftnoir_tracker_smoothtrack.cpp (Lines 185-259)
"""

import math
import struct
from typing import List, Optional, Tuple

FRAME_SIZE = 48  # 6 * sizeof(double)
NUM_POSE_ELEMENTS = 6
STRUCT_FORMAT = "<6d"  # 6 IEEE 754 64-bit doubles, little-endian

# Axis offset value mapping defined in ftnoir_tracker_smoothtrack.cpp:240-246
AXIS_OFFSET_VALUES = [0, 90, -90, 180, -180]

DEFAULT_ANDROID_PORT = 4242
DEFAULT_IOS_PORT = 47047


def pack_pose(x: float, y: float, z: float, yaw: float, pitch: float, roll: float) -> bytes:
    """Packs 6 double precision components into a 48-byte binary frame."""
    return struct.pack(STRUCT_FORMAT, float(x), float(y), float(z), float(yaw), float(pitch), float(roll))


def unpack_pose(data: bytes) -> Tuple[float, float, float, float, float, float]:
    """Unpacks a 48-byte binary frame into 6 double precision components.
    
    Raises struct.error if len(data) != 48.
    """
    if len(data) != FRAME_SIZE:
        raise ValueError(f"Invalid frame size {len(data)}, expected {FRAME_SIZE}")
    return struct.unpack(STRUCT_FORMAT, data)


def is_pose_valid(pose: Tuple[float, ...]) -> bool:
    """Validates pose components against NaN and Infinity matching std::fpclassify.
    
    In C++ (ftnoir_tracker_smoothtrack.cpp:209-216):
        const int val = std::fpclassify(pose[i]);
        if (val == FP_NAN || val == FP_INFINITE) { ok = false; break; }
    """
    if len(pose) != NUM_POSE_ELEMENTS:
        return False
    for v in pose:
        if math.isnan(v) or math.isinf(v):
            return False
    return True


def apply_axis_offsets(pose: Tuple[float, ...], add_yaw_idx: int = 0, add_pitch_idx: int = 0, add_roll_idx: int = 0) -> List[float]:
    """Applies user orientation axis offsets (Yaw, Pitch, Roll) per OpenTrack convention.
    
    In C++ (ftnoir_tracker_smoothtrack.cpp:240-258):
        const int values[] = { 0, 90, -90, 180, -180 };
        data[Yaw + i] += values[k];
    """
    result = list(pose)
    indices = [add_yaw_idx, add_pitch_idx, add_roll_idx]
    for i, k in enumerate(indices):
        if 0 <= k < len(AXIS_OFFSET_VALUES):
            result[3 + i] += AXIS_OFFSET_VALUES[k]
    return result


class SimulatedStreamParser:
    """Exact behavioral reference model of the unified smoothtrack::run() loop.
    
    Verifies that stream buffer draining, freshest frame preservation,
    and corrupted frame rejection match OpenTrack's implementation.
    """

    def __init__(self):
        self.buffer = bytearray()
        self.last_recv_pose = [0.0] * NUM_POSE_ELEMENTS
        self.frames_processed = 0
        self.frames_rejected = 0

    def feed_bytes(self, chunk: bytes) -> None:
        """Appends bytes received from socket and processes full 48-byte frames."""
        self.buffer.extend(chunk)
        self.drain_frames()

    def drain_frames(self) -> None:
        """Drains all available 48-byte frames in the buffer, keeping the freshest valid pose."""
        while len(self.buffer) >= FRAME_SIZE:
            frame_bytes = bytes(self.buffer[:FRAME_SIZE])
            del self.buffer[:FRAME_SIZE]

            try:
                pose = unpack_pose(frame_bytes)
            except Exception:
                self.frames_rejected += 1
                continue

            if is_pose_valid(pose):
                self.last_recv_pose = list(pose)
                self.frames_processed += 1
            else:
                self.frames_rejected += 1

    def get_current_pose(self, add_yaw_idx: int = 0, add_pitch_idx: int = 0, add_roll_idx: int = 0) -> List[float]:
        """Returns the current pose with applied axis offsets, matching smoothtrack::data()."""
        return apply_axis_offsets(tuple(self.last_recv_pose), add_yaw_idx, add_pitch_idx, add_roll_idx)
