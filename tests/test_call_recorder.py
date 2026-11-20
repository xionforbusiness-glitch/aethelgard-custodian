import array
import os
import sys
import tempfile
from pathlib import Path

# Add repo root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import call_recorder
import voice_tools_registry

def test_recorder_and_registry():
    print("Testing CallAudioRecorder...")
    rec = call_recorder.CallAudioRecorder(
        call_id="vm_test_call",
        peer_id=1021125594,
        direction="incoming",
    )

    sample_rate = 48000
    in_pcm = array.array("h", [1000] * sample_rate).tobytes()
    out_pcm = array.array("h", [2000] * sample_rate).tobytes()

    for i in range(0, len(in_pcm), 960):
        rec.feed_incoming(in_pcm[i : i + 960])
        rec.feed_outgoing(out_pcm[i : i + 960])

    mixed = rec.mix_audio()
    assert len(mixed) >= sample_rate * 2, "Mixed PCM length failure"

    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
        ogg_path = Path(f.name)

    try:
        ok = rec.encode_ogg_opus(mixed, ogg_path)
        assert ok, "FFmpeg encode failed"
        assert ogg_path.stat().st_size > 100, "Ogg file too small"
        print(f"✅ Audio mix and encode passed: {ogg_path.stat().st_size} bytes")
    finally:
        if ogg_path.exists():
            os.remove(ogg_path)

    # Test registry
    tools = voice_tools_registry.get_voice_tool_declarations()
    names = [f.name for t in tools for f in t.function_declarations]
    assert "place_telegram_call" in names, "place_telegram_call not found in registry"
    print("✅ Tool declarations verified: place_telegram_call is active!")

    print("ALL TESTS PASSED ON VM!")

if __name__ == "__main__":
    test_recorder_and_registry()
