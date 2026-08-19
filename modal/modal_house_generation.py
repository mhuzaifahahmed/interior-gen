"""
Modal deployment for "Build a House" exterior render generation.

Reuses the exact same proven stack as the room-redesign Kaggle notebook
(RealVisXL_V5.0 + xinsir/controlnet-depth-sdxl-1.0, ControlNet-depth
conditioning to preserve the input photo's real structure/geometry) - that
combination is a general photoreal SDXL setup, not room-specific, so it
works for exterior architecture too. Only the prompt content differs
(exterior massing/architecture instead of interior redesign) - the prompt
itself is composed on the interior-gen backend side (app/pipeline/
house_prompts.py), not here - this endpoint just takes a fully-composed
prompt string and generates from it, same contract as the existing Kaggle
/generate endpoint.

WHY THIS IS SIMPLER THAN THE ROOM-REDESIGN BATCH SETUP:
- Only ONE image is generated per request (Build a House produces one
  exterior render, not 3 tiers) - no batching, no CUDA OOM risk from
  multi-image VAE decode, no submit-then-poll job pattern needed.
- Modal endpoints don't sit behind a free Cloudflare quick tunnel, so
  there's no ~100s hard timeout to design around either - a single
  blocking request is fine here.

DEPLOY:
    modal deploy modal_house_generation.py

That prints a permanent HTTPS URL - no tunnel, no notebook session to keep
alive, no restart-and-get-a-new-URL cycle.
"""

import base64
import io

import modal

app = modal.App("house-generation")

# Model weights are cached in a Modal Volume so a cold container doesn't
# re-download ~14GB from Hugging Face on every wake-up - only the first
# deploy pays that cost, every request after reuses the cached weights.
model_cache = modal.Volume.from_name("house-gen-model-cache", create_if_missing=True)
CACHE_DIR = "/cache"

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install(
        "diffusers",
        "transformers",
        "accelerate",
        "controlnet_aux",
        "torch",
        "torchvision",
        "fastapi[standard]",
        "pillow",
        "safetensors",
    )
    .env({"HF_HUB_CACHE": CACHE_DIR})
)


@app.cls(
    image=image,
    gpu="T4",
    timeout=600,  # generous - a real single-image test on this exact stack took ~45-78s
    startup_timeout=300,  # separate budget for @modal.enter() (model load) vs
                          # request execution - real observed cold starts run
                          # up to ~274s uncached / ~120s with the snapshot below.
    volumes={CACHE_DIR: model_cache},
    scaledown_window=120,  # keep the container warm for 2 min after the last request,
                           # so back-to-back generations don't all pay the cold-start cost
    enable_memory_snapshot=True,  # ALPHA feature (per Modal's own docs) - snapshots the
                                   # container's memory (including the loaded model on the
                                   # GPU) after @modal.enter(snap=True) runs once, so future
                                   # cold starts restore from that snapshot instead of
                                   # re-running model load from scratch. Real risk: alpha
                                   # status, can behave unpredictably (Modal's own docs flag
                                   # multi-GPU/torch.compile issues) - being tried here
                                   # specifically to measure real cold-start impact.
    experimental_options={"enable_gpu_snapshot": True},
)
class HouseGenerator:
    @modal.enter(snap=True)
    def load(self):
        import torch
        from diffusers import ControlNetModel, DPMSolverMultistepScheduler, StableDiffusionXLControlNetPipeline
        from transformers import pipeline as hf_pipeline

        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.benchmark = True

        controlnet = ControlNetModel.from_pretrained(
            "xinsir/controlnet-depth-sdxl-1.0",
            torch_dtype=torch.float16,
            use_safetensors=True,
        )

        self.pipe = StableDiffusionXLControlNetPipeline.from_pretrained(
            "SG161222/RealVisXL_V5.0",
            controlnet=controlnet,
            torch_dtype=torch.float16,
            use_safetensors=True,
            low_cpu_mem_usage=True,
        )
        self.pipe.scheduler = DPMSolverMultistepScheduler.from_config(
            self.pipe.scheduler.config, use_karras_sigmas=True
        )
        self.pipe.to("cuda")
        self.pipe.enable_attention_slicing()
        self.pipe.enable_vae_slicing()

        # Kept on CPU deliberately - same reasoning as the room-redesign
        # notebook: depth estimation isn't the speed bottleneck, no need to
        # spend GPU VRAM keeping it resident.
        self.depth_estimator = hf_pipeline("depth-estimation", model="Intel/dpt-hybrid-midas")

        self.torch = torch

    def _get_depth_map(self, image):
        depth = self.depth_estimator(image)["depth"]
        return depth.convert("RGB")

    @modal.method()
    def generate(
        self,
        image_base64: str,
        prompt: str,
        negative_prompt: str = (
            "warped windows, broken roofline, floating structure, wrong story count, "
            "duplicated buildings, smeared materials, low quality, blurry, distorted geometry"
        ),
        num_inference_steps: int = 30,
        guidance_scale: float = 5.0,
        control_scale: float = 0.80,
    ) -> str:
        from PIL import Image

        image_bytes = base64.b64decode(image_base64)
        input_image = Image.open(io.BytesIO(image_bytes)).convert("RGB").resize((1024, 1024))

        depth_image = self._get_depth_map(input_image)

        output = self.pipe(
            prompt=prompt,
            negative_prompt=negative_prompt,
            image=depth_image,
            controlnet_conditioning_scale=control_scale,
            num_inference_steps=num_inference_steps,
            guidance_scale=guidance_scale,
        ).images[0]

        buffered = io.BytesIO()
        output.save(buffered, format="JPEG", quality=85)
        return base64.b64encode(buffered.getvalue()).decode("utf-8")

    @modal.fastapi_endpoint(method="POST", docs=True)
    def generate_endpoint(self, request: dict):
        try:
            result_b64 = self.generate.local(
                image_base64=request["image_base64"],
                prompt=request["prompt"],
                negative_prompt=request.get(
                    "negative_prompt",
                    "warped windows, broken roofline, floating structure, wrong story count, "
                    "duplicated buildings, smeared materials, low quality, blurry, distorted geometry",
                ),
                num_inference_steps=request.get("num_inference_steps", 30),
                guidance_scale=request.get("guidance_scale", 5.0),
                control_scale=request.get("control_scale", 0.80),
            )
            return {"status": "success", "generated_image_base64": result_b64}
        except Exception as e:
            return {"status": "error", "detail": str(e)}
