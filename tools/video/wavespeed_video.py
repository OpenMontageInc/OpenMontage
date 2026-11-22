"""Wan 3.0 text-to-video and image-to-video through WaveSpeed AI."""

from tools.schema_media import SchemaMedia


class WaveSpeedVideo(SchemaMedia):
    name = "wavespeed_video"
    provider = "wavespeed"
    capability = "video_generation"
    credential = "WAVESPEED_API_KEY"
    dependencies = ["env:WAVESPEED_API_KEY"]
    install_instructions = "Configure WAVESPEED_API_KEY."
    catalog_file = "wavespeed_models.json"
    capabilities = ["text_to_video", "image_to_video"]
    supports = dict.fromkeys(capabilities + ["native_audio"], True)
    best_for = ["Wan 3.0 text and image to video with native audio on WaveSpeed"]
    models = {
        "wan-3.0": {
            op: "alibaba/wan-3.0/" + op.replace("_", "-") for op in capabilities
        }
    }
