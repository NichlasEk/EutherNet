# Local AI Compatibility Map

EutherNet may document local AI profile compatibility without owning the model
files. The rule is: keep durable knowledge here, keep machine-local checkouts,
weights, generated caches, and patched vendor files outside git.

This file is a safe map for operators and future EutherNet automation. It uses
stable service/profile names, expected symptoms, and patch intent instead of
absolute host paths.

## Scope

- EutherPunk owns the chat and image gateway behavior.
- EutherSight SecondSight consumes EutherPunk image jobs and packages successful
  artifacts as `.jox`.
- ComfyUI profile files and model weights are local runtime state.
- EutherNet records which runtime profiles are known to need compatibility
  shims after model, ComfyUI, PyTorch, or Transformers upgrades.

## Compatibility Config Map

```toml
[profiles.sensenova_u1_8b]
service = "comfyui"
selector_model = "sensenova-u1-8b"
used_by = ["EutherPunk image edit", "EutherSight SecondSight"]
status = "requires_local_compatibility_shims"

[[profiles.sensenova_u1_8b.shims]]
id = "neo_llm_config_rope_theta"
symptom = "SenseNova_SM_Model fails with: NEOLLMConfig object has no attribute rope_theta"
intent = "Preserve rope_theta from the model config on dense and MoE NEO LLM config objects."
safe_to_commit_vendor_file = false

[[profiles.sensenova_u1_8b.shims]]
id = "qwen3_rotary_embedding_default_rope_parameters"
symptom = "SenseNova_SM_Model fails with: Qwen3RotaryEmbedding object has no attribute compute_default_rope_parameters"
intent = "Provide a compatibility wrapper for newer Transformers weight initialization."
safe_to_commit_vendor_file = false

[jobs.eutherpunk_image]
default_timeout_seconds = 720
minimum_timeout_seconds = 720
error_reporting = "ComfyUI history status_str must surface immediately when status is not success."

[jobs.secondsight]
poll_timeout_seconds = 720
expected_success_outputs = ["image", "metadata", "jox"]
```

## Operational Notes

When a SecondSight job appears stuck:

1. Check the EutherSight card state. `waiting_comfy` means the image job reached
   EutherPunk and is waiting for ComfyUI.
2. Check the ComfyUI queue. A running prompt means the GPU job is active. An
   empty queue with a waiting EutherPunk job usually means the prompt already
   moved to history.
3. Check ComfyUI history for the prompt id. `status_str = "error"` should be
   treated as a final failure and surfaced to the UI.
4. If history shows a SenseNova compatibility AttributeError, compare it with
   the shim map above before changing prompts, JOX packaging, or timeout values.

## Persistence Rule

Do not commit local ComfyUI profile files or model weights into EutherNet.
Instead, commit:

- the service/profile identity,
- the symptom,
- the intended compatibility behavior,
- the validation command or observation,
- and the owning application behavior that depends on it.

This keeps EutherNet useful for rebuilds without turning it into a mirror of
machine-local AI installations.
