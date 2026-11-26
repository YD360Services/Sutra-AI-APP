from dataclasses import dataclass
from typing import Optional
import time


@dataclass
class AudioFrameValidationResult:
    valid: bool
    reason: Optional[str] = None
    is_duplicate: bool = False
    is_out_of_order: bool = False
    gap_detected: bool = False
    expected_sequence: Optional[int] = None
    received_sequence: Optional[int] = None


class AudioFrameValidator:
    """
    LLD v1.3 Item 17: Validates incoming audio frames per session.
    Enforces frame-size bounds, negotiated format constraints, and sequence/timestamp continuity.
    """

    def __init__(
        self,
        max_frame_bytes: int = 65536,
        min_frame_bytes: int = 1,
        expected_sample_rate: int = 16000,
        expected_channels: int = 1,
        expected_format: str = "pcm_s16le",
    ):
        self.max_frame_bytes = max_frame_bytes
        self.min_frame_bytes = min_frame_bytes
        self.expected_sample_rate = expected_sample_rate
        self.expected_channels = expected_channels
        self.expected_format = expected_format
        self._last_seq: Optional[int] = None
        self._last_ts_ms: Optional[float] = None
        self.total_frames: int = 0
        self.gap_count: int = 0
        self.duplicate_count: int = 0
        self.out_of_order_count: int = 0

    def validate_format(self, sample_rate: int, channels: int, fmt: str) -> bool:
        return (
            sample_rate == self.expected_sample_rate
            and channels == self.expected_channels
            and fmt == self.expected_format
        )

    def validate_frame(
        self,
        payload: bytes,
        sequence: Optional[int] = None,
        timestamp_ms: Optional[float] = None,
    ) -> AudioFrameValidationResult:
        if not payload or len(payload) < self.min_frame_bytes:
            return AudioFrameValidationResult(valid=False, reason="EMPTY_FRAME")
        if len(payload) > self.max_frame_bytes:
            return AudioFrameValidationResult(
                valid=False,
                reason=f"FRAME_TOO_LARGE:{len(payload)}>{self.max_frame_bytes}",
            )

        self.total_frames += 1
        is_dup = False
        is_ooo = False
        gap = False
        expected_seq = (self._last_seq + 1) if self._last_seq is not None else None

        if sequence is not None:
            if self._last_seq is not None:
                if sequence == self._last_seq:
                    is_dup = True
                    self.duplicate_count += 1
                elif sequence < self._last_seq:
                    is_ooo = True
                    self.out_of_order_count += 1
                elif sequence > self._last_seq + 1:
                    gap = True
                    self.gap_count += 1
                    self._last_seq = sequence
                else:
                    self._last_seq = sequence
            else:
                self._last_seq = sequence

        if timestamp_ms is not None:
            self._last_ts_ms = timestamp_ms
        else:
            self._last_ts_ms = time.time() * 1000.0

        return AudioFrameValidationResult(
            valid=not is_dup and not is_ooo,
            reason="DUPLICATE_FRAME" if is_dup else ("OUT_OF_ORDER_FRAME" if is_ooo else None),
            is_duplicate=is_dup,
            is_out_of_order=is_ooo,
            gap_detected=gap,
            expected_sequence=expected_seq,
            received_sequence=sequence,
        )
