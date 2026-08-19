"""
Modal deployment for room-redesign (economical/mid/premium tier) image
generation - replaces the Kaggle-notebook-plus-Cloudflare-tunnel setup with
a permanent, reliable hosted endpoint.

Same proven model stack as the Kaggle notebook (RealVisXL_V5.0 +
xinsir/controlnet-depth-sdxl-1.0, attention/VAE slicing, Karras scheduler) -
only the hosting changed, not the AI logic.

WHY THIS IS SIMPLER THAN THE KAGGLE VERSION:
- No Cloudflare quick-tunnel ~100s timeout to design around, so the batch
  endpoint can go back to being ONE blocking call instead of the
  submit-then-poll job pattern Kaggle needed. Modal's own function `timeout`
  param (set generously below) is the only limit, and it's not a proxy
  killing the connection out from under a successful request.
- No manual notebook restarts / new tunnel URLs - `modal deploy` gives a
  permanent URL that survives forever (or until redeployed).

WHAT'S KEPT FROM THE KAGGLE LESSONS (these were hardware-driven, not
platform-driven - the same T4 GPU class has the same VRAM ceiling here):
- attention_slicing + vae_slicing (fixes UNet/decode OOM)
- 768x768 for the 3-tier batch call specifically (1024x1024 OOM'd during VAE
  decode on a T4 even with slicing enabled - single-image /generate stays at
  1024, matching the Kaggle setup)
- OOM-safe sequential fallback in the batch path

DEPLOY:
    modal deploy modal_room_redesign.py
"""

import base64
import io

import modal

app = modal.App("room-redesign")

model_cache = modal.Volume.from_name("room-redesign-model-cache", create_if_missing=True)
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
    timeout=600,
    startup_timeout=300,  # separate budget for @modal.enter() itself (model
                          # load) vs actual request execution - without this,
                          # `timeout` alone covers both combined (see Modal's
                          # docs on startup_timeout, added in v1.1.4). Real
                          # observed cold starts run up to ~274s uncached /
                          # ~120s with the snapshot below - 300s is headroom
                          # over both.
    volumes={CACHE_DIR: model_cache},
    scaledown_window=120,
    enable_memory_snapshot=True,  # ALPHA feature (per Modal's own docs) -
                                   # snapshots the container's memory
                                   # (including the loaded GPU model) after
                                   # @modal.enter(snap=True) runs once, so
                                   # future cold starts restore from that
                                   # snapshot instead of reloading from
                                   # scratch. Real, live-measured result on
                                   # the identical model in
                                   # modal_house_generation.py: cold start
                                   # dropped from ~274s to ~120s (roughly
                                   # halved) - not eliminated, and alpha
                                   # status means it could behave
                                   # unpredictably (Modal's own docs flag
                                   # multi-GPU/torch.compile issues), but
                                   # free and worth keeping on.
    experimental_options={"enable_gpu_snapshot": True},
)
class RoomRedesigner:
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

        self.depth_estimator = hf_pipeline("depth-estimation", model="Intel/dpt-hybrid-midas")
        self.torch = torch

    def _get_depth_map(self, images):
        if isinstance(images, list):
            results = self.depth_estimator(images)
            return [r["depth"].convert("RGB") for r in results]
        return self.depth_estimator(images)["depth"].convert("RGB")

    # ---- Single-image generation (mirrors Kaggle's /generate, 1024x1024) ----

    @modal.method()
    def generate(
        self,
        image_base64: str,
        prompt: str,
        negative_prompt: str = "ugly, low quality, distorted, blurry, bad architecture",
        num_inference_steps: int = 20,
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
                    "negative_prompt", "ugly, low quality, distorted, blurry, bad architecture"
                ),
                num_inference_steps=request.get("num_inference_steps", 20),
                guidance_scale=request.get("guidance_scale", 5.0),
                control_scale=request.get("control_scale", 0.80),
            )
            return {"status": "success", "generated_image_base64": result_b64}
        except Exception as e:
            return {"status": "error", "detail": str(e)}

    # ---- Batch generation (3 tiers, 768x768, OOM-safe fallback) ----
    # ONE blocking call, not job+poll - Modal has no ~100s proxy timeout to
    # dodge, so the simpler synchronous shape Kaggle originally tried (before
    # the tunnel-timeout discovery) is safe to use here.

    def _run_true_batch(self, depth_maps, prompts, negatives, control_scale, num_inference_steps, guidance_scale):
        return self.pipe(
            prompt=prompts,
            negative_prompt=negatives,
            image=depth_maps,
            controlnet_conditioning_scale=control_scale,
            num_inference_steps=num_inference_steps,
            guidance_scale=guidance_scale,
        ).images

    def _run_sequential_fallback(
        self, depth_maps, prompts, negatives, control_scale, num_inference_steps, guidance_scale
    ):
        import gc

        outputs = []
        for depth_image, prompt, negative in zip(depth_maps, prompts, negatives):
            gc.collect()
            self.torch.cuda.empty_cache()
            img = self.pipe(
                prompt=prompt,
                negative_prompt=negative,
                image=depth_image,
                controlnet_conditioning_scale=control_scale,
                num_inference_steps=num_inference_steps,
                guidance_scale=guidance_scale,
            ).images[0]
            outputs.append(img)
        return outputs

    @modal.method()
    def generate_batch(
        self,
        items: list[dict],
        num_inference_steps: int = 20,
        guidance_scale: float = 5.0,
        control_scale: float = 0.80,
        resolution: int = 768,
    ) -> list[str]:
        from PIL import Image

        raw_images = []
        prompts = []
        negatives = []
        for item in items:
            image_bytes = base64.b64decode(item["image_base64"])
            img = Image.open(io.BytesIO(image_bytes)).convert("RGB").resize((resolution, resolution))
            raw_images.append(img)
            prompts.append(item["prompt"])
            negatives.append(item.get("negative_prompt", "ugly, low quality, distorted, blurry, bad architecture"))

        depth_maps = self._get_depth_map(raw_images)

        try:
            outputs = self._run_true_batch(
                depth_maps, prompts, negatives, control_scale, num_inference_steps, guidance_scale
            )
        except self.torch.cuda.OutOfMemoryError:
            import gc

            gc.collect()
            self.torch.cuda.empty_cache()
            outputs = self._run_sequential_fallback(
                depth_maps, prompts, negatives, control_scale, num_inference_steps, guidance_scale
            )

        results = []
        for img in outputs:
            buffered = io.BytesIO()
            img.save(buffered, format="JPEG", quality=85)
            results.append(base64.b64encode(buffered.getvalue()).decode("utf-8"))
        return results

    @modal.fastapi_endpoint(method="POST", docs=True)
    def generate_batch_endpoint(self, request: dict):
        try:
            results = self.generate_batch.local(
                items=request["items"],
                num_inference_steps=request.get("num_inference_steps", 20),
                guidance_scale=request.get("guidance_scale", 5.0),
                control_scale=request.get("control_scale", 0.80),
                resolution=request.get("resolution", 768),
            )
            return {"status": "success", "generated_images_base64": results}
        except Exception as e:
            return {"status": "error", "detail": str(e)}
