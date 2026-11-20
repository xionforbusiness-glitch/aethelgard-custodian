#!/usr/bin/env python3
"""
🌪️ Aethelgard World Simulation Matrix: Chaos & Network Degradation Engine
========================================================================
Simulates real-world acoustic impairments, packet loss, transmission jitter,
airport ambient background noise, and abrupt socket tear-downs.
"""

from __future__ import annotations

import array
import asyncio
import math
import random
import time
from dataclasses import dataclass
from typing import AsyncGenerator, Iterator, List, Optional, Tuple


@dataclass
class ChaosProfile:
    name: str
    packet_loss_rate: float = 0.0          # 0.0 - 1.0 (e.g. 0.08 for 8% drop)
    burst_loss_prob: float = 0.0           # Probability of initiating a burst drop
    burst_loss_len: Tuple[int, int] = (2, 5) # Min and max consecutive chunks to drop
    jitter_ms_min: float = 0.0             # Minimum simulated network jitter delay
    jitter_ms_max: float = 0.0             # Maximum simulated network jitter delay
    background_noise_level: float = 0.0    # 0.0 (clean) to 1.0 (heavy airport noise)
    clipping_threshold: float = 1.0        # 1.0 (normal) to 0.4 (harsh clipping)
    unexpected_hangup_after_s: Optional[float] = None


# Standard Real-World Profiles
PROFILE_PRISTINE = ChaosProfile(
    name="pristine_fiber",
    packet_loss_rate=0.0,
    jitter_ms_min=0.0,
    jitter_ms_max=5.0,
)

PROFILE_AIRPORT_CELLULAR = ChaosProfile(
    name="damascus_airport_3g_edge",
    packet_loss_rate=0.06,
    burst_loss_prob=0.03,
    burst_loss_len=(2, 6),
    jitter_ms_min=20.0,
    jitter_ms_max=160.0,
    background_noise_level=0.15,
    clipping_threshold=0.85,
)

PROFILE_HIGH_CHAOS_STORM = ChaosProfile(
    name="high_chaos_storm",
    packet_loss_rate=0.18,
    burst_loss_prob=0.08,
    burst_loss_len=(4, 12),
    jitter_ms_min=50.0,
    jitter_ms_max=320.0,
    background_noise_level=0.30,
    clipping_threshold=0.60,
    unexpected_hangup_after_s=6.5,
)


class ChaosEngine:
    """Injects acoustic degradation and network latency into audio streams."""

    def __init__(self, profile: Optional[ChaosProfile] = None):
        self.profile = profile or PROFILE_PRISTINE
        self._in_burst = False
        self._burst_remaining = 0
        self._total_chunks_processed = 0
        self._total_chunks_dropped = 0
        self._total_jitter_delay_ms = 0.0

    def get_metrics(self) -> dict:
        total = max(1, self._total_chunks_processed)
        return {
            "profile": self.profile.name,
            "total_chunks": self._total_chunks_processed,
            "dropped_chunks": self._total_chunks_dropped,
            "effective_loss_rate": round(self._total_chunks_dropped / total, 4),
            "avg_jitter_delay_ms": round(self._total_jitter_delay_ms / total, 2),
        }

    def should_drop_chunk(self) -> bool:
        """Determines if the next audio frame should be dropped by packet loss."""
        self._total_chunks_processed += 1

        if self._in_burst:
            self._burst_remaining -= 1
            if self._burst_remaining <= 0:
                self._in_burst = False
            self._total_chunks_dropped += 1
            return True

        if self.profile.burst_loss_prob > 0 and random.random() < self.profile.burst_loss_prob:
            self._in_burst = True
            low, high = self.profile.burst_loss_len
            self._burst_remaining = random.randint(low, high)
            self._total_chunks_dropped += 1
            return True

        if self.profile.packet_loss_rate > 0 and random.random() < self.profile.packet_loss_rate:
            self._total_chunks_dropped += 1
            return True

        return False

    def calculate_jitter_delay_s(self) -> float:
        """Calculates simulated network packet transit jitter."""
        if self.profile.jitter_ms_max <= 0:
            return 0.0
        delay_ms = random.uniform(self.profile.jitter_ms_min, self.profile.jitter_ms_max)
        self._total_jitter_delay_ms += delay_ms
        return delay_ms / 1000.0

    def apply_acoustic_noise_and_clipping(self, pcm_bytes: bytes, sample_width: int = 2) -> bytes:
        """Applies airport background ambient hiss, rumble, and microphone overdrive clipping."""
        if not pcm_bytes or (self.profile.background_noise_level <= 0 and self.profile.clipping_threshold >= 1.0):
            return pcm_bytes

        try:
            samples = array.array("h")
            samples.frombytes(pcm_bytes)
            max_val = 32767
            clip_limit = int(max_val * self.profile.clipping_threshold)
            noise_amp = int(max_val * self.profile.background_noise_level * 0.25)

            # Fast vectorized sample perturbation
            for i in range(len(samples)):
                val = samples[i]
                if noise_amp > 0:
                    # Low-frequency airport rumble + Gaussian white noise
                    rumble = int(noise_amp * 0.6 * math.sin(i * 0.05))
                    hiss = random.randint(-noise_amp // 2, noise_amp // 2)
                    val = val + rumble + hiss
                
                # Apply microphone clipping
                if val > clip_limit:
                    val = clip_limit
                elif val < -clip_limit:
                    val = -clip_limit

                samples[i] = val

            return samples.tobytes()
        except Exception:
            return pcm_bytes

    async def degrade_stream(
        self,
        chunk_generator: AsyncGenerator[bytes, None],
    ) -> AsyncGenerator[bytes, None]:
        """Asynchronously degrades an audio stream in real-time."""
        start_time = time.monotonic()

        async for chunk in chunk_generator:
            if self.profile.unexpected_hangup_after_s:
                if (time.monotonic() - start_time) >= self.profile.unexpected_hangup_after_s:
                    # Abrupt line drop
                    break

            if self.should_drop_chunk():
                # Frame lost in transit
                continue

            jitter_s = self.calculate_jitter_delay_s()
            if jitter_s > 0:
                await asyncio.sleep(jitter_s)

            yield self.apply_acoustic_noise_and_clipping(chunk)
