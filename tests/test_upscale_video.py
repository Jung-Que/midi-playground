from pathlib import Path
import unittest

from config import Config
from tools.upscale_video import UpscaleError, build_ffmpeg_command, default_output_path


class UpscaleVideoTests(unittest.TestCase):
    def test_creator_resolution_presets_include_2k_and_4k_vertical_masters(self):
        self.assertIn((1440, 2560), Config.RESOLUTION_PRESETS)
        self.assertIn((2160, 3840), Config.RESOLUTION_PRESETS)

    def test_default_output_does_not_replace_source(self):
        source = Path("short.mp4")
        self.assertEqual(default_output_path(source, 2), Path("short_2x_edit_master.mp4"))

    def test_ffmpeg_command_uses_lanczos_and_preserves_audio(self):
        command = build_ffmpeg_command(
            "ffmpeg", Path("input.mp4"), Path("output.mp4"), scale=2,
        )
        filter_value = command[command.index("-vf") + 1]
        self.assertIn("scale=trunc(iw*2/2)*2:trunc(ih*2/2)*2:flags=lanczos", filter_value)
        self.assertIn("unsharp=5:5:0.40", filter_value)
        self.assertEqual(command[command.index("-c:a") + 1], "copy")
        self.assertEqual(command[-2], "-n")

    def test_invalid_scale_is_rejected(self):
        with self.assertRaises(UpscaleError):
            build_ffmpeg_command(
                "ffmpeg", Path("input.mp4"), Path("output.mp4"), scale=5,
            )


if __name__ == "__main__":
    unittest.main()
