"""NVIDIA Build Free Endpoint listing captured 2026-10-23.

Catalog labels are discovery hints, NOT confirmed commercial entitlements,
working API routes or permission to redistribute inference to end users.
Only exact raw IDs are matched; specialized endpoints need own transports.
"""
NVIDIA_BUILD_FREE_ENDPOINT_IDS = frozenset('''
nvidia/nemotron-3.5-bolt-e4b
nvidia/nemotron-3.5-rerank-8b
nvidia/nemotron-3.5-embed-1b
nvidia/nemotron-3.5-embed-8b
minimaxai/minimax-m3.1-flash
ipd/rfd4-proteina
nvidia/nemotron-3.5-super-vl-120b-a12b
stepfun-ai/step-5-preview
nvidia/kumo-tabular
nvidia/body-pose
deepseek-ai/deepseek-v4.1-flash
nvidia/kumo-relational
z-ai/glm-5.3
z-ai/glm-5.3-flash
nvidia/nemotron-3.5-lightning-30b-a3b
meta/muse-glimmer-30b
nvidia/riva-translate-4b-instruct-v2
nvidia/ising-calibration-1.5-31b
nvidia/nemotron-3-embed-1b
poolside/laguna-xs-2.1
google/diffusiongemma-26b-a4b-it
nvidia/nemotron-3-ultra-550b-a55b
nvidia/nemotron-3.5-content-safety
nvidia/cosmos3-nano
nvidia/cosmos3-nano-reasoner
nvidia/nemotron-3-nano-omni-30b-a3b-reasoning
nvidia/synthetic-video-detector
nvidia/active-speaker-detection
nvidia/ising-calibration-1-35b-a3b
google/gemma-4-31b-it
nvidia/nemotron-voicechat
nvidia/cosmos-transfer2.5-2b
nvidia/streampetr
nvidia/llama-3.1-nemotron-safety-guard-8b-v3
openai/gpt-oss-20b
meta/llama-guard-4-12b
nvidia/bnr
nvidia/magpie-tts-zeroshot
nvidia/sparsedrive
nvidia/bevformer
nvidia/studiovoice
meta/llama-3.2-11b-vision-instruct
meta/llama-3.2-90b-vision-instruct
google/google-paligemma
'''.strip().splitlines())

def is_nvidia_build_free_endpoint(raw_model_id: str) -> bool:
    return raw_model_id in NVIDIA_BUILD_FREE_ENDPOINT_IDS
