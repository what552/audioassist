"""Instance-scoped compatibility for the pinned qwen-asr 0.0.6 runtime."""
from __future__ import annotations
import inspect


def install_audio_window_masks(model) -> list:
    """Restore the omitted non-FA2 mask (QwenLM/Qwen3-ASR PR #103).

    No global monkey-patches, tensor caches, or source copies. Removing the
    handles releases the closures that refer to each encoder.
    """
    handles = []
    found = False
    try:
        for encoder in model.modules():
            if type(encoder).__name__ != "Qwen3ASRAudioEncoder":
                continue
            found = True
            if encoder.config._attn_implementation == "flash_attention_2":
                continue

            def add_mask(layer, args, kwargs, encoder=encoder):
                if (len(args) > 2 and args[2] is not None) or kwargs.get("attention_mask") is not None:
                    return args, kwargs
                hidden = args[0] if args else kwargs["hidden_states"]
                lengths = args[1] if len(args) > 1 else kwargs["cu_seqlens"]
                mask = encoder._prepare_attention_mask(hidden, lengths)
                if len(args) > 2:
                    args = (*args[:2], mask, *args[3:])
                else:
                    kwargs = {**kwargs, "attention_mask": mask}
                return args, kwargs

            for layer in encoder.layers:
                handles.append(layer.register_forward_pre_hook(add_mask, with_kwargs=True))
        if not found:
            raise RuntimeError("Unsupported Qwen audio encoder layout; review the pinned compatibility shim")
        return handles
    except Exception:
        for handle in handles:
            handle.remove()
        raise


class GenerationObserver:
    """Observe this model's generated tokens without changing generation."""

    def __init__(self, model):
        self.model = model
        self.original = model.generate
        self.records = []
        self.had_override = "generate" in vars(model)
        # Qwen's outer generate() supplies its own EOS default to the thinker;
        # the thinker's generation_config.eos_token_id is actually None.
        parameter = inspect.signature(self.original).parameters.get("eos_token_id")
        eos = parameter.default if parameter is not None else inspect.Parameter.empty
        if eos is inspect.Parameter.empty:
            eos = model.generation_config.eos_token_id
        self.eos_ids = set(eos if isinstance(eos, (list, tuple)) else [eos])
        if not self.eos_ids or None in self.eos_ids:
            raise RuntimeError("Qwen generation has no configured EOS token")
        model.generate = self.generate

    def generate(self, *args, **kwargs):
        result = self.original(*args, **kwargs)
        prompt_length = kwargs["input_ids"].shape[1]
        rows = result.sequences[:, prompt_length:].tolist()
        limit = int(kwargs["max_new_tokens"])
        override = kwargs.get("eos_token_id")
        eos_ids = self.eos_ids if override is None else set(override if isinstance(override, (list, tuple)) else [override])
        for row in rows:
            ending = next((i for i, token in enumerate(row) if token in eos_ids), None)
            capped = ending is None and len(row) >= limit
            self.records.append({
                # Match MLX's count: the trailing EOS sentinel is excluded.
                "generated_tokens": ending if ending is not None else len(row),
                "max_new_tokens": limit,
                "finish_reason": "eos" if ending is not None else ("length" if capped else "unknown"),
                "truncated": capped,
            })
        return result

    def close(self):
        if self.model is not None:
            if self.had_override:
                self.model.generate = self.original
            else:
                del self.model.generate
        self.model = None
        self.original = None
        self.records = []
