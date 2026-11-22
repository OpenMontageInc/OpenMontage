"""Seedream 5.5 generation and editing through WaveSpeed AI."""

from tools.schema_media import SchemaMedia


class WaveSpeedImage(SchemaMedia):
    name = "wavespeed_image"
    provider = "wavespeed"
    capability = "image_generation"
    credential = "WAVESPEED_API_KEY"
    dependencies = ["env:WAVESPEED_API_KEY"]
    install_instructions = "Configure WAVESPEED_API_KEY."
    catalog_file = "wavespeed_models.json"
    capabilities = ["text_to_image", "image_edit"]
    supports = {"image_edit": True, "multiple_reference_images": True}
    best_for = ["Seedream 5.5 generation and multi-reference edits on WaveSpeed"]
    models = {
        "seedream-v5.5": {
            "generate": "bytedance/seedream-v5.5",
            "edit": "bytedance/seedream-v5.5/edit",
        }
    }
