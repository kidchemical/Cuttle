"""Curated, versioned local-AI landscape catalog.

This intentionally has no Flask, Cuttle-home, or network dependency.  It is a
small replaceable research snapshot: routes only expose its normalized payload.
When a maintained feed becomes available, replace this module behind the same
``dashboard()`` contract rather than putting provider or retailer details in a
page or route.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable


CATALOG_SCHEMA_VERSION = 1
CATALOG_VERSION = "2026-11-08"
RESEARCHED_AT = "2026-11-08T00:00:00+00:00"


def _src(label: str, url: str) -> Dict[str, str]:
    return {"label": label, "url": url}


# Scores are deliberately inputs rather than claimed benchmark results.  They
# make the default sort explainable and let a future feed use the same contract.
MODEL_WEIGHTS = {
    "capability_score": 0.45,
    "local_fit_score": 0.25,
    "coding_focus_score": 0.15,
    "license_score": 0.10,
    "recency_score": 0.05,
}
HARDWARE_WEIGHTS = {
    "cost_score": 0.25,
    "efficiency_score": 0.20,
    "capacity_score": 0.25,
    "speed_score": 0.20,
    "compatibility_score": 0.10,
}
BUILD_WEIGHTS = {
    "cost_score": 0.25,
    "efficiency_score": 0.15,
    "capacity_score": 0.30,
    "speed_score": 0.15,
    "compatibility_score": 0.15,
}


MODELS = [
    {
        "id": "qwen3-6-27b", "name": "Qwen3.6 27B", "publisher": "Qwen",
        "family": "Qwen3.6", "license": "Apache-2.0", "release_year": 2026,
        "params_b": 27, "active_params_b": 27, "context_k": 262,
        "recommended_vram_gb": 24, "weight_size_q4_gb": 16,
        "agent_benchmark": "SWE-bench Verified (publisher)", "agent_score": 77.2,
        "coding_benchmark": "Terminal-Bench 2 (publisher)", "coding_score": 59.3,
        "capability_score": 94, "local_fit_score": 88, "coding_focus_score": 94,
        "license_score": 100, "recency_score": 100,
        "sources": [_src("Weights and model card", "https://huggingface.co/Qwen/Qwen3.6-27B")],
    },
    {
        "id": "qwen3-6-35b-a3b", "name": "Qwen3.6 35B-A3B", "publisher": "Qwen",
        "family": "Qwen3.6", "license": "Apache-2.0", "release_year": 2026,
        "params_b": 35, "active_params_b": 3, "context_k": 262,
        "recommended_vram_gb": 32, "weight_size_q4_gb": 21,
        "agent_benchmark": "SWE-bench Verified (publisher)", "agent_score": 73.4,
        "coding_benchmark": "Terminal-Bench 2 (publisher)", "coding_score": 51.5,
        "capability_score": 91, "local_fit_score": 82, "coding_focus_score": 93,
        "license_score": 100, "recency_score": 100,
        "sources": [_src("Weights and model card", "https://huggingface.co/Qwen/Qwen3.6-35B-A3B")],
    },
    {
        "id": "devstral-small-2", "name": "Devstral Small 2 24B", "publisher": "Mistral AI",
        "family": "Devstral", "license": "Apache-2.0", "release_year": 2025,
        "params_b": 24, "active_params_b": 24, "context_k": 256,
        "recommended_vram_gb": 24, "weight_size_q4_gb": 14,
        "agent_benchmark": "SWE-bench Verified (publisher)", "agent_score": 68.0,
        "coding_benchmark": "Terminal-Bench 2 (publisher)", "coding_score": 22.5,
        "capability_score": 85, "local_fit_score": 92, "coding_focus_score": 100,
        "license_score": 100, "recency_score": 88,
        "sources": [_src("Weights and model card", "https://huggingface.co/mistralai/Devstral-Small-2-24B-Instruct-2512")],
    },
    {
        "id": "qwen3-5-27b", "name": "Qwen3.5 27B", "publisher": "Qwen",
        "family": "Qwen3.5", "license": "Apache-2.0", "release_year": 2026,
        "params_b": 27, "active_params_b": 27, "context_k": 262,
        "recommended_vram_gb": 24, "weight_size_q4_gb": 16,
        "agent_benchmark": "SWE-bench Verified (publisher)", "agent_score": 72.4,
        "coding_benchmark": "Terminal-Bench 2 (publisher)", "coding_score": 41.6,
        "capability_score": 89, "local_fit_score": 88, "coding_focus_score": 90,
        "license_score": 100, "recency_score": 96,
        "sources": [_src("Weights and model card", "https://huggingface.co/Qwen/Qwen3.5-27B")],
    },
    {
        "id": "qwen3-coder-30b-a3b", "name": "Qwen3-Coder 30B-A3B", "publisher": "Qwen",
        "family": "Qwen3-Coder", "license": "Apache-2.0", "release_year": 2025,
        "params_b": 30.5, "active_params_b": 3.3, "context_k": 262,
        "recommended_vram_gb": 24, "weight_size_q4_gb": 18,
        "agent_benchmark": "Not normalized in source card", "agent_score": None,
        "coding_benchmark": "Code-specialized release", "coding_score": None,
        "capability_score": 86, "local_fit_score": 87, "coding_focus_score": 100,
        "license_score": 100, "recency_score": 90,
        "sources": [_src("Weights and model card", "https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct")],
    },
    {
        "id": "gpt-oss-20b", "name": "gpt-oss-20b", "publisher": "OpenAI",
        "family": "gpt-oss", "license": "Apache-2.0", "release_year": 2025,
        "params_b": 21, "active_params_b": 3.6, "context_k": 128,
        "recommended_vram_gb": 16, "weight_size_q4_gb": 13,
        "agent_benchmark": "Tool-use release evaluation", "agent_score": None,
        "coding_benchmark": "LiveCodeBench v6 (reported)", "coding_score": 74.6,
        "capability_score": 84, "local_fit_score": 97, "coding_focus_score": 87,
        "license_score": 100, "recency_score": 90,
        "sources": [_src("Weights and model card", "https://huggingface.co/openai/gpt-oss-20b")],
    },
    {
        "id": "qwen3-5-9b", "name": "Qwen3.5 9B", "publisher": "Qwen",
        "family": "Qwen3.5", "license": "Apache-2.0", "release_year": 2026,
        "params_b": 9, "active_params_b": 9, "context_k": 262,
        "recommended_vram_gb": 12, "weight_size_q4_gb": 6,
        "agent_benchmark": "No repository-agent score", "agent_score": None,
        "coding_benchmark": "LiveCodeBench v6 (publisher)", "coding_score": 65.6,
        "capability_score": 73, "local_fit_score": 100, "coding_focus_score": 78,
        "license_score": 100, "recency_score": 96,
        "sources": [_src("Weights and model card", "https://huggingface.co/Qwen/Qwen3.5-9B")],
    },
    {
        "id": "qwen3-5-4b", "name": "Qwen3.5 4B", "publisher": "Qwen",
        "family": "Qwen3.5", "license": "Apache-2.0", "release_year": 2026,
        "params_b": 4, "active_params_b": 4, "context_k": 262,
        "recommended_vram_gb": 8, "weight_size_q4_gb": 3,
        "agent_benchmark": "No repository-agent score", "agent_score": None,
        "coding_benchmark": "LiveCodeBench v6 (publisher)", "coding_score": 55.8,
        "capability_score": 61, "local_fit_score": 100, "coding_focus_score": 68,
        "license_score": 100, "recency_score": 96,
        "sources": [_src("Weights and model card", "https://huggingface.co/Qwen/Qwen3.5-4B")],
    },
    {
        "id": "glm-4-5-air", "name": "GLM-4.5-Air", "publisher": "Z.ai",
        "family": "GLM-4.5", "license": "MIT", "release_year": 2025,
        "params_b": 106, "active_params_b": 12, "context_k": 128,
        "recommended_vram_gb": 80, "weight_size_q4_gb": 63,
        "agent_benchmark": "Agentic evaluation (publisher)", "agent_score": None,
        "coding_benchmark": "Repository / tool use emphasis", "coding_score": None,
        "capability_score": 91, "local_fit_score": 44, "coding_focus_score": 90,
        "license_score": 100, "recency_score": 88,
        "sources": [_src("Weights and model card", "https://huggingface.co/zai-org/GLM-4.5-Air")],
    },
    {
        "id": "deepseek-v3-1", "name": "DeepSeek-V3.1", "publisher": "DeepSeek",
        "family": "DeepSeek-V3", "license": "MIT", "release_year": 2025,
        "params_b": 671, "active_params_b": 37, "context_k": 128,
        "recommended_vram_gb": 320, "weight_size_q4_gb": 400,
        "agent_benchmark": "Tool-use / agentic release", "agent_score": None,
        "coding_benchmark": "Code and tool-use release", "coding_score": None,
        "capability_score": 96, "local_fit_score": 12, "coding_focus_score": 88,
        "license_score": 100, "recency_score": 85,
        "sources": [_src("Weights and model card", "https://huggingface.co/deepseek-ai/DeepSeek-V3.1")],
    },
    {
        "id": "kimi-k2-instruct", "name": "Kimi K2 Instruct", "publisher": "Moonshot AI",
        "family": "Kimi K2", "license": "Modified MIT", "release_year": 2025,
        "params_b": 1000, "active_params_b": 32, "context_k": 128,
        "recommended_vram_gb": 640, "weight_size_q4_gb": 600,
        "agent_benchmark": "Agentic release evaluation", "agent_score": None,
        "coding_benchmark": "Tool-use / coding release", "coding_score": None,
        "capability_score": 97, "local_fit_score": 8, "coding_focus_score": 91,
        "license_score": 80, "recency_score": 85,
        "sources": [_src("Weights and model card", "https://huggingface.co/moonshotai/Kimi-K2-Instruct")],
    },
    {
        "id": "qwen3-5-122b-a10b", "name": "Qwen3.5 122B-A10B", "publisher": "Qwen",
        "family": "Qwen3.5", "license": "Apache-2.0", "release_year": 2026,
        "params_b": 122, "active_params_b": 10, "context_k": 262,
        "recommended_vram_gb": 80, "weight_size_q4_gb": 73,
        "agent_benchmark": "SWE-bench Verified (publisher)", "agent_score": 72.0,
        "coding_benchmark": "Terminal-Bench 2 (publisher)", "coding_score": 49.4,
        "capability_score": 92, "local_fit_score": 45, "coding_focus_score": 91,
        "license_score": 100, "recency_score": 96,
        "sources": [_src("Weights and model card", "https://huggingface.co/Qwen/Qwen3.5-122B-A10B")],
    },
    {
        "id": "gpt-oss-120b", "name": "gpt-oss-120b", "publisher": "OpenAI",
        "family": "gpt-oss", "license": "Apache-2.0", "release_year": 2025,
        "params_b": 117, "active_params_b": 5.1, "context_k": 128,
        "recommended_vram_gb": 80, "weight_size_q4_gb": 73,
        "agent_benchmark": "SWE-bench Verified (reported)", "agent_score": 62.0,
        "coding_benchmark": "Native tool use", "coding_score": None,
        "capability_score": 87, "local_fit_score": 45, "coding_focus_score": 86,
        "license_score": 100, "recency_score": 90,
        "sources": [_src("Weights and model card", "https://huggingface.co/openai/gpt-oss-120b")],
    },
]


HARDWARE = [
    {
        "id": "rtx-5090", "name": "GeForce RTX 5090", "vendor": "NVIDIA", "category": "consumer GPU",
        "introduced_year": 2025, "price_usd": 1999, "price_basis": "launch MSRP", "memory_gb": 32,
        "memory_type": "GDDR7", "bandwidth_gbps": 1792, "tdp_w": 575, "measured_tps": 290,
        "tps_note": "Llama 2 7B Q4_0, llama.cpp community run; not cross-device comparable", "link_type": "PCIe only",
        "link_notes": "No NVLink; multi-GPU requires software sharding over PCIe.", "software": "CUDA",
        "cost_score": 75, "efficiency_score": 66, "capacity_score": 70, "speed_score": 100, "compatibility_score": 100,
        "sources": [_src("NVIDIA specification", "https://www.nvidia.com/en-us/geforce/graphics-cards/50-series/rtx-5090/"),
                    _src("llama.cpp benchmark discussion", "https://github.com/ggml-org/llama.cpp/discussions/15013")],
    },
    {
        "id": "rtx-3090", "name": "GeForce RTX 3090", "vendor": "NVIDIA", "category": "used consumer GPU",
        "introduced_year": 2020, "price_usd": 800, "price_basis": "typical used-market reference", "memory_gb": 24,
        "memory_type": "GDDR6X", "bandwidth_gbps": 936, "tdp_w": 350, "measured_tps": None,
        "tps_note": "Add a measured result with model, quantization and backend before comparing speed.", "link_type": "2-way NVLink",
        "link_notes": "NVLink bridge is possible on supported cards; inference runtime still decides whether memory can be sharded.", "software": "CUDA",
        "cost_score": 95, "efficiency_score": 70, "capacity_score": 58, "speed_score": 67, "compatibility_score": 96,
        "sources": [_src("NVIDIA specification", "https://www.nvidia.com/en-us/geforce/graphics-cards/30-series/rtx-3090-3090ti/")],
    },
    {
        "id": "rtx-pro-5000-bw", "name": "RTX PRO 5000 Blackwell", "vendor": "NVIDIA", "category": "workstation GPU",
        "introduced_year": 2025, "price_usd": 4500, "price_basis": "system listing allocation", "memory_gb": 72,
        "memory_type": "GDDR7 ECC", "bandwidth_gbps": None, "tdp_w": 300, "measured_tps": None,
        "tps_note": "No normalized local TPS included in this snapshot.", "link_type": "PCIe only",
        "link_notes": "Use tensor/model parallel software; aggregate VRAM is not automatic shared memory.", "software": "CUDA",
        "cost_score": 42, "efficiency_score": 79, "capacity_score": 94, "speed_score": 90, "compatibility_score": 100,
        "sources": [_src("Example live dual-GPU workstation", "https://www.newegg.com/p/N82E16889850012")],
    },
    {
        "id": "a100-80gb", "name": "NVIDIA A100 80GB", "vendor": "NVIDIA", "category": "datacenter accelerator",
        "introduced_year": 2020, "price_usd": 8500, "price_basis": "used-market reference; varies by form factor", "memory_gb": 80,
        "memory_type": "HBM2e", "bandwidth_gbps": 1935, "tdp_w": 300, "measured_tps": None,
        "tps_note": "Compare only a matched backend/model benchmark.", "link_type": "NVLink (form-factor dependent)",
        "link_notes": "PCIe supports bridge configurations; SXM requires compatible HGX infrastructure.", "software": "CUDA",
        "cost_score": 48, "efficiency_score": 82, "capacity_score": 96, "speed_score": 88, "compatibility_score": 100,
        "sources": [_src("NVIDIA datasheet", "https://resources.nvidia.com/en-us-gpu/nvidia-a100-datashee-1")],
    },
    {
        "id": "tesla-p40", "name": "Tesla P40", "vendor": "NVIDIA", "category": "used datacenter accelerator",
        "introduced_year": 2016, "price_usd": 450, "price_basis": "used-market reference", "memory_gb": 24,
        "memory_type": "GDDR5", "bandwidth_gbps": 346, "tdp_w": 250, "measured_tps": None,
        "tps_note": "Older passive card; validate runtime and driver support before buying.", "link_type": "PCIe only",
        "link_notes": "Passive cooling needs server-grade airflow; use software model splitting.", "software": "CUDA (legacy constraints)",
        "cost_score": 92, "efficiency_score": 37, "capacity_score": 58, "speed_score": 35, "compatibility_score": 58,
        "sources": [_src("NVIDIA product brief", "https://www.nvidia.com/content/dam/en-zz/Solutions/Data-Center/tesla-product-literature/Tesla-P40-Product-Brief.pdf")],
    },
    {
        "id": "dgx-spark", "name": "NVIDIA DGX Spark (GB10)", "vendor": "NVIDIA", "category": "AI desktop appliance",
        "introduced_year": 2025, "price_usd": 6950, "price_basis": "NVIDIA marketplace listed price; availability varies", "memory_gb": 128,
        "memory_type": "unified LPDDR5x", "bandwidth_gbps": 273, "tdp_w": 140, "measured_tps": 33.1,
        "tps_note": "gpt-oss-120b MXFP4, vLLM community report, concurrency 1; not comparable to 7B figures.", "link_type": "ConnectX networking",
        "link_notes": "Two systems cooperate over network; memory is not one automatic shared pool.", "software": "CUDA / DGX OS",
        "cost_score": 62, "efficiency_score": 95, "capacity_score": 100, "speed_score": 57, "compatibility_score": 93,
        "sources": [_src("NVIDIA specification", "https://www.nvidia.com/en-us/products/workstations/dgx-spark/"),
                    _src("Marketplace listing", "https://marketplace.nvidia.com/en-us/enterprise/personal-ai-supercomputers/nvidia-dgx-spark/"),
                    _src("Matched benchmark report", "https://forums.developer.nvidia.com/t/new-tool-llama-benchy-llama-bench-style-benchmarking-for-any-llm-backend-vllm-sglang-llama-cpp-etc/356698")],
    },
    {
        "id": "mac-studio-m5-ultra", "name": "Mac Studio M5 Ultra", "vendor": "Apple", "category": "unified-memory desktop",
        "introduced_year": 2026, "price_usd": 5499, "price_basis": "Apple M5 Ultra starting price", "memory_gb": 512,
        "memory_type": "unified memory", "bandwidth_gbps": 1200, "tdp_w": None, "measured_tps": None,
        "tps_note": "Use MLX / Metal runs with exact model and quantization; Apple does not publish comparable board TDP.", "link_type": "network only",
        "link_notes": "Multiple Macs can distribute a workload, not form one transparent RAM pool.", "software": "Metal / MLX",
        "cost_score": 58, "efficiency_score": 92, "capacity_score": 100, "speed_score": 76, "compatibility_score": 75,
        "sources": [_src("Apple technical specifications", "https://www.apple.com/mac-studio/specs/"),
                    _src("Apple availability and pricing", "https://www.apple.com/newsroom/2026/08/apple-introduces-new-mac-studio-with-m5-max-and-m5-ultra/")],
    },
    {
        "id": "radeon-ai-pro-r9700", "name": "Radeon AI PRO R9700", "vendor": "AMD", "category": "workstation GPU",
        "introduced_year": 2025, "price_usd": 2500, "price_basis": "system listing allocation", "memory_gb": 32,
        "memory_type": "GDDR6", "bandwidth_gbps": None, "tdp_w": 300, "measured_tps": None,
        "tps_note": "Add a ROCm benchmark using an exact supported stack before comparing speed.", "link_type": "PCIe only",
        "link_notes": "Multi-GPU requires ROCm/runtime support; separate 32GB allocations are not automatically pooled.", "software": "ROCm",
        "cost_score": 72, "efficiency_score": 73, "capacity_score": 70, "speed_score": 74, "compatibility_score": 70,
        "sources": [_src("Live triple-GPU workstation", "https://www.newegg.com/p/N82E16859991069")],
    },
    {
        "id": "rx-7900-xtx", "name": "Radeon RX 7900 XTX", "vendor": "AMD", "category": "consumer GPU",
        "introduced_year": 2022, "price_usd": 999, "price_basis": "launch MSRP", "memory_gb": 24,
        "memory_type": "GDDR6", "bandwidth_gbps": 960, "tdp_w": 355, "measured_tps": None,
        "tps_note": "Verify ROCm support for the operating system and backend before relying on a price/performance comparison.", "link_type": "PCIe only",
        "link_notes": "Software sharding only; no NVLink-equivalent fabric.", "software": "ROCm (support varies)",
        "cost_score": 83, "efficiency_score": 65, "capacity_score": 58, "speed_score": 66, "compatibility_score": 60,
        "sources": [_src("AMD specification", "https://www.amd.com/en/products/graphics/desktops/radeon/7000-series/amd-radeon-rx-7900xtx.html")],
    },
    {
        "id": "arc-pro-b70", "name": "Intel Arc Pro B70", "vendor": "Intel", "category": "workstation GPU",
        "introduced_year": 2025, "price_usd": 700, "price_basis": "system listing allocation", "memory_gb": 32,
        "memory_type": "GDDR6", "bandwidth_gbps": None, "tdp_w": 200, "measured_tps": None,
        "tps_note": "Benchmark support is runtime-specific; use a known-good oneAPI/IPEX or llama.cpp stack.", "link_type": "PCIe only",
        "link_notes": "Multi-GPU is software-controlled; validate driver and framework support.", "software": "oneAPI / IPEX",
        "cost_score": 90, "efficiency_score": 84, "capacity_score": 70, "speed_score": 55, "compatibility_score": 50,
        "sources": [_src("Example live triple-GPU workstation", "https://www.newegg.com/p/N82E16859991068")],
    },
]


def _part(role: str, name: str, url: str, note: str = "") -> Dict[str, str]:
    return {"role": role, "name": name, "url": url, "note": note}


_R9700_LISTING = "https://www.newegg.com/p/N82E16859991069"
_ARC_LISTING = "https://www.newegg.com/p/N82E16859991068"
_RTX5000_LISTING = "https://www.newegg.com/p/N82E16889850012"
_RTX4500_LISTING = "https://www.newegg.com/p/N82E16889850015"

BUILDS = [
    {
        "id": "mac-studio-m5-ultra-256", "name": "Mac Studio M5 Ultra · 256GB / 2TB", "vendor": "Apple",
        "approach": "quiet unified-memory appliance", "availability": "Orderable at Apple; delivery depends on location",
        "verified_at": RESEARCHED_AT, "price_usd": 10319, "price_basis": "Apple education-store listed 256GB / 2TB configuration; consumer price may differ",
        "memory_gb": 256, "single_device_memory_gb": 256, "typical_draw_w": None, "measured_tps": None,
        "model_fit": "Large quantized models can use unified memory; reserve capacity for macOS and KV cache.",
        "unification": "Network/distributed only — no automatic pooled memory across Macs.",
        "cost_score": 58, "efficiency_score": 92, "capacity_score": 94, "speed_score": 75, "compatibility_score": 74,
        "listing": _src("Apple order page", "https://www.apple.com/us-edu/shop/buy-mac/mac-studio/m5-ultra-chip-36-core-cpu-80-core-gpu-256gb-memory-2tb-storage"),
        "parts": [
            _part("System", "Mac Studio M5 Ultra", "https://www.apple.com/shop/buy-mac/mac-studio"),
            _part("CPU / GPU", "M5 Ultra · 36-core CPU / 80-core GPU", "https://www.apple.com/mac-studio/specs/"),
            _part("Unified memory", "256GB unified memory", "https://www.apple.com/mac-studio/specs/"),
            _part("Storage", "2TB SSD", "https://www.apple.com/mac-studio/specs/"),
            _part("Power / enclosure", "Integrated Mac Studio enclosure and power supply", "https://www.apple.com/mac-studio/specs/"),
        ],
    },
    {
        "id": "abs-rtx-pro-4500", "name": "ABS Zaurion · RTX PRO 4500", "vendor": "ABS / Newegg",
        "approach": "single-GPU CUDA workstation", "availability": "Add to cart / build-to-order at last check",
        "verified_at": RESEARCHED_AT, "price_usd": 10599, "price_basis": "live Newegg listing price; tax and configuration can change",
        "memory_gb": 32, "single_device_memory_gb": 32, "typical_draw_w": None, "measured_tps": None,
        "model_fit": "A practical CUDA path for roughly 24–32B Q4 models; use CPU offload for larger models.",
        "unification": "Single GPU; later expansion is PCIe/software-specific.",
        "cost_score": 50, "efficiency_score": 75, "capacity_score": 70, "speed_score": 82, "compatibility_score": 100,
        "listing": _src("Newegg configuration and listing", _RTX4500_LISTING),
        "parts": [
            _part("GPU", "NVIDIA RTX PRO 4500 Blackwell · 32GB", _RTX4500_LISTING, "Seller configuration"),
            _part("CPU", "AMD Ryzen 9 9950X3D", _RTX4500_LISTING, "Seller configuration"),
            _part("Motherboard", "AM5 motherboard (exact model not published by seller)", _RTX4500_LISTING, "Confirm with seller before purchase"),
            _part("Memory", "64GB ECC DDR5", _RTX4500_LISTING, "Seller configuration"),
            _part("Storage", "2 × 2TB NVMe SSD", _RTX4500_LISTING, "Seller configuration"),
            _part("Power supply", "1000W 80 Plus Platinum", _RTX4500_LISTING, "Seller configuration"),
            _part("Case", "Mid-tower workstation", _RTX4500_LISTING, "Seller configuration"),
        ],
    },
    {
        "id": "abs-3x-r9700", "name": "ABS Zaurion Ruby · 3× Radeon AI PRO R9700", "vendor": "ABS / Newegg",
        "approach": "ROCm multi-GPU workstation", "availability": "Add to cart / build-to-order at last check",
        "verified_at": RESEARCHED_AT, "price_usd": 18599, "price_basis": "live Newegg listing price; page has shown promotional variation",
        "memory_gb": 96, "single_device_memory_gb": 32, "typical_draw_w": None, "measured_tps": None,
        "model_fit": "70B-class Q4 is the seller's stated target; ensure the chosen ROCm runtime can shard the exact model.",
        "unification": "96GB aggregate across three 32GB GPUs; software model parallelism, not one physical pool.",
        "cost_score": 62, "efficiency_score": 70, "capacity_score": 93, "speed_score": 75, "compatibility_score": 72,
        "listing": _src("Newegg configuration and listing", _R9700_LISTING),
        "parts": [
            _part("GPU", "3 × AMD Radeon AI PRO R9700 · 3 × 32GB GDDR6", _R9700_LISTING, "Seller configuration"),
            _part("CPU", "AMD Ryzen Threadripper PRO 9965WX · 24 cores", _R9700_LISTING),
            _part("Motherboard", "Gigabyte MH53-G40", _R9700_LISTING),
            _part("Memory", "128GB ECC registered DDR5", _R9700_LISTING),
            _part("OS storage", "2TB M.2 NVMe SSD", _R9700_LISTING),
            _part("Data storage", "2TB M.2 NVMe SSD", _R9700_LISTING),
            _part("Power supply", "Single 2000W ATX 80 Plus Gold", _R9700_LISTING),
            _part("Barebone / case", "Gigabyte W773-H5D-AA01 GPU workstation", _R9700_LISTING),
        ],
    },
    {
        "id": "abs-3x-arc-pro-b70", "name": "ABS Zaurion · 3× Intel Arc Pro B70", "vendor": "ABS / Newegg",
        "approach": "lower-cost multi-GPU workstation", "availability": "Add to cart / build-to-order at last check",
        "verified_at": RESEARCHED_AT, "price_usd": 13999, "price_basis": "live Newegg listing price at research time",
        "memory_gb": 96, "single_device_memory_gb": 32, "typical_draw_w": None, "measured_tps": None,
        "model_fit": "Potential 70B-class Q4 capacity only when the selected Intel stack supports tensor/model parallelism.",
        "unification": "96GB aggregate across PCIe devices; software model splitting required.",
        "cost_score": 75, "efficiency_score": 80, "capacity_score": 93, "speed_score": 57, "compatibility_score": 50,
        "listing": _src("Newegg configuration and listing", _ARC_LISTING),
        "parts": [
            _part("GPU", "3 × Intel Arc Pro B70 · 3 × 32GB", _ARC_LISTING, "Seller configuration"),
            _part("CPU", "AMD Ryzen Threadripper PRO 9965WX · 24 cores", _ARC_LISTING),
            _part("Motherboard", "WRX90 workstation board (exact model confirm with seller)", _ARC_LISTING),
            _part("Memory", "128GB ECC registered DDR5", _ARC_LISTING),
            _part("Storage", "2 × 2TB M.2 NVMe SSD", _ARC_LISTING),
            _part("Power supply", "2000W 80 Plus Gold", _ARC_LISTING),
            _part("Case", "GPU workstation tower", _ARC_LISTING),
        ],
    },
    {
        "id": "abs-2x-rtx-pro-5000", "name": "ABS Zaurion · 2× RTX PRO 5000 Blackwell", "vendor": "ABS / Newegg",
        "approach": "high-memory CUDA workstation", "availability": "Add to cart / build-to-order at last check",
        "verified_at": RESEARCHED_AT, "price_usd": 37599, "price_basis": "live Newegg listing price at research time",
        "memory_gb": 144, "single_device_memory_gb": 72, "typical_draw_w": None, "measured_tps": None,
        "model_fit": "One 72GB card can accommodate many 70B Q4 configurations; use supported sharding for larger models.",
        "unification": "144GB aggregate over PCIe; no automatic unified VRAM pool.",
        "cost_score": 30, "efficiency_score": 79, "capacity_score": 100, "speed_score": 91, "compatibility_score": 100,
        "listing": _src("Newegg configuration and listing", _RTX5000_LISTING),
        "parts": [
            _part("GPU", "2 × NVIDIA RTX PRO 5000 Blackwell · 2 × 72GB", _RTX5000_LISTING, "Seller configuration"),
            _part("CPU", "Intel Xeon W9-3575X · 44 cores", _RTX5000_LISTING),
            _part("Motherboard", "Gigabyte MW83-RP0", _RTX5000_LISTING),
            _part("Memory", "128GB ECC registered DDR5 RDIMM", _RTX5000_LISTING),
            _part("OS storage", "2TB NVMe M.2", _RTX5000_LISTING),
            _part("Data storage", "4TB NVMe M.2", _RTX5000_LISTING),
            _part("Power supply", "2000W 80 Plus Gold", _RTX5000_LISTING),
            _part("Barebone / case", "Gigabyte W773-W80 workstation", _RTX5000_LISTING),
        ],
    },
]


_CONFIGS = {
    "local-models": {
        "title": "Open-weight coding & agentic models",
        "description": "Curated local-model research snapshot. Benchmark fields remain source-specific; the rank is a transparent decision aid, not a universal benchmark.",
        "rows": MODELS,
        "weights": MODEL_WEIGHTS,
        "filter_fields": ("publisher", "license", "recommended_vram_gb"),
        "axes": ("overall_rank", "overall_score", "agent_score", "coding_score", "params_b", "active_params_b", "context_k", "recommended_vram_gb", "weight_size_q4_gb", "release_year"),
        "columns": ("overall_rank", "name", "publisher", "params_b", "active_params_b", "recommended_vram_gb", "agent_score", "coding_score", "overall_score"),
    },
    "local-ai-hardware": {
        "title": "Local AI hardware",
        "description": "Capacity, power, price and speed are kept separate. TPS is only comparable when the model, quantization, backend, context and concurrency match.",
        "rows": HARDWARE,
        "weights": HARDWARE_WEIGHTS,
        "filter_fields": ("vendor", "category", "software", "link_type"),
        "axes": ("overall_rank", "overall_score", "price_usd", "memory_gb", "bandwidth_gbps", "tdp_w", "measured_tps", "introduced_year", "cost_score", "efficiency_score", "capacity_score", "speed_score"),
        "columns": ("overall_rank", "name", "category", "price_usd", "memory_gb", "bandwidth_gbps", "tdp_w", "measured_tps", "overall_score"),
    },
    "local-ai-builds": {
        "title": "Purchasable local-AI builds",
        "description": "Verified shopping snapshot: each row links to a current seller/manufacturer listing and exposes the advertised component configuration. Prices and availability change at checkout.",
        "rows": BUILDS,
        "weights": BUILD_WEIGHTS,
        "filter_fields": ("vendor", "approach", "availability"),
        "axes": ("overall_rank", "overall_score", "price_usd", "memory_gb", "single_device_memory_gb", "typical_draw_w", "measured_tps", "cost_score", "efficiency_score", "capacity_score", "speed_score"),
        "columns": ("overall_rank", "name", "approach", "price_usd", "memory_gb", "single_device_memory_gb", "typical_draw_w", "overall_score"),
    },
}


def dashboard_ids() -> tuple[str, ...]:
    return tuple(_CONFIGS)


def _score(row: Dict[str, Any], weights: Dict[str, float]) -> float:
    return round(sum(float(row.get(key, 0) or 0) * weight for key, weight in weights.items()), 1)


def _rank(rows: Iterable[Dict[str, Any]], weights: Dict[str, float]) -> list[Dict[str, Any]]:
    ranked = [dict(row, overall_score=_score(row, weights)) for row in rows]
    ranked.sort(key=lambda row: (-row["overall_score"], row["name"].lower()))
    for index, row in enumerate(ranked, 1):
        row["overall_rank"] = index
    return ranked


def _filters(rows: list[Dict[str, Any]], fields: tuple[str, ...]) -> Dict[str, list[Any]]:
    return {
        field: sorted({row[field] for row in rows if row.get(field) is not None}, key=lambda item: str(item).lower())
        for field in fields
    }


def dashboard(dash_id: str) -> Dict[str, Any]:
    """Return the versioned contract consumed by the local-AI dashboard owner."""
    config = _CONFIGS[dash_id]
    rows = _rank(deepcopy(config["rows"]), config["weights"])
    return {
        "success": True,
        "id": dash_id,
        "title": config["title"],
        "dataset": "curated-research-snapshot",
        "catalog_schema_version": CATALOG_SCHEMA_VERSION,
        "catalog_version": CATALOG_VERSION,
        "researched_at": RESEARCHED_AT,
        "description": config["description"],
        "ranking": {
            "label": "Overall decision score",
            "weights": dict(config["weights"]),
            "note": "Weighted editorial inputs. It is a sortable decision aid, not a controlled cross-vendor benchmark.",
        },
        "filters": _filters(rows, config["filter_fields"]),
        "filter_fields": list(config["filter_fields"]),
        "axes": list(config["axes"]),
        "columns": list(config["columns"]),
        "rows": rows,
        "stats": {"entries": len(rows), "ranked": len(rows), "with_measured_tps": sum(row.get("measured_tps") is not None for row in rows)},
    }
