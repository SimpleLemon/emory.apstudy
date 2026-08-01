import json
import os
import unittest
from unittest.mock import MagicMock, patch

from services import giphy


class GiphyTests(unittest.TestCase):
    def test_api_key_preserves_empty_default_and_trims_configured_values(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(giphy.api_key(), "")
        with patch.dict(os.environ, {"GIPHY_API_KEY": "  key-123  "}, clear=True):
            self.assertEqual(giphy.api_key(), "key-123")

    def test_missing_key_keeps_configuration_error(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(giphy.GiphyError, "GIF sharing is not configured"):
                giphy.resolve_gif("gif-1")

    def test_resolve_gif_accepts_only_https_giphy_media(self):
        payload = {
            "data": {
                "title": "Reaction",
                "images": {
                    "original": {
                        "webp": "https://media.giphy.com/original.webp",
                        "width": "640",
                        "height": "360",
                    },
                    "fixed_width": {"webp": "https://media.giphy.com/preview.webp"},
                },
            }
        }
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(payload).encode()

        with patch.dict(os.environ, {"GIPHY_API_KEY": "key"}, clear=True), patch.object(
            giphy.urllib.request, "urlopen", return_value=response
        ) as urlopen:
            resolved = giphy.resolve_gif("gif-1", "hello")

        self.assertEqual(resolved["url"], "https://media.giphy.com/original.webp")
        self.assertEqual(resolved["preview_url"], "https://media.giphy.com/preview.webp")
        self.assertEqual((resolved["width"], resolved["height"]), (640, 360))
        self.assertIn("api_key=key", urlopen.call_args.args[0])

    def test_resolve_gif_rejects_non_giphy_media_hosts(self):
        payload = {
            "data": {
                "images": {
                    "original": {"url": "https://example.test/image.gif"},
                }
            }
        }
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(payload).encode()

        with patch.dict(os.environ, {"GIPHY_API_KEY": "key"}, clear=True), patch.object(
            giphy.urllib.request, "urlopen", return_value=response
        ):
            with self.assertRaisesRegex(giphy.GiphyError, "That GIF is unavailable"):
                giphy.resolve_gif("gif-1")


if __name__ == "__main__":
    unittest.main()
