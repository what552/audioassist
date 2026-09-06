"""Tiny torch objects exercise the instance shims; no model downloads."""
from types import SimpleNamespace
from unittest.mock import patch
import gc
import weakref

import pytest
import torch

from src.qwen_compat import GenerationObserver, install_audio_window_masks


class Layer(torch.nn.Module):
    def forward(self, hidden_states, cu_seqlens, attention_mask=None):
        return attention_mask


class Qwen3ASRAudioEncoder(torch.nn.Module):
    def __init__(self, attention="sdpa"):
        super().__init__()
        self.config = SimpleNamespace(_attn_implementation=attention)
        self.layers = torch.nn.ModuleList([Layer(), Layer()])
        self.calls = 0

    def _prepare_attention_mask(self, hidden, lengths):
        self.calls += 1
        mask = torch.full((1, 1, len(hidden), len(hidden)), float("-inf"))
        for start, end in zip(lengths[:-1], lengths[1:]):
            mask[..., start:end, start:end] = 0
        return mask


@pytest.mark.parametrize("mode", ["eager", "sdpa"])
def test_mask_has_independent_windows_and_is_not_global(mode):
    encoder, untouched = Qwen3ASRAudioEncoder(mode), Qwen3ASRAudioEncoder(mode)
    handles = install_audio_window_masks(encoder)
    hidden, lengths = torch.zeros(4, 2), torch.tensor([0, 2, 4])
    mask = encoder.layers[0](hidden, lengths)
    assert mask[0, 0, 0, 1] == 0
    assert mask[0, 0, 0, 2].isneginf()
    assert untouched.layers[0](hidden, lengths) is None
    # Keyword calling, no shared per-inference mask cache.
    second = encoder.layers[1](hidden_states=hidden, cu_seqlens=lengths)
    assert torch.equal(mask, second) and mask is not second
    for handle in handles:
        handle.remove()
    assert encoder.layers[0](hidden, lengths) is None


def test_existing_positional_and_keyword_masks_are_preserved():
    encoder = Qwen3ASRAudioEncoder()
    install_audio_window_masks(encoder)
    hidden, lengths, mask = torch.zeros(2, 2), torch.tensor([0, 2]), object()
    assert encoder.layers[0](hidden, lengths, mask) is mask
    assert encoder.layers[0](hidden, lengths, attention_mask=mask) is mask
    assert encoder.calls == 0
    assert encoder.layers[0](hidden, lengths, None) is not None


def test_flash_attention_is_unchanged_and_unknown_layout_fails():
    encoder = Qwen3ASRAudioEncoder("flash_attention_2")
    assert install_audio_window_masks(encoder) == []
    with pytest.raises(RuntimeError, match="layout"):
        install_audio_window_masks(Layer())


def test_partial_install_removes_already_registered_hooks():
    encoder = Qwen3ASRAudioEncoder()
    with patch.object(encoder.layers[1], "register_forward_pre_hook", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError, match="boom"):
            install_audio_window_masks(encoder)
    assert not encoder.layers[0]._forward_pre_hooks


class Model(torch.nn.Module):
    def __init__(self, tokens):
        super().__init__()
        self.tokens = tokens
        self.thinker = SimpleNamespace(generation_config=SimpleNamespace(eos_token_id=None))

    def generate(self, *, input_ids, max_new_tokens, eos_token_id=(2, 3)):
        return SimpleNamespace(sequences=torch.cat((input_ids, torch.tensor([self.tokens])), dim=1))


@pytest.mark.parametrize("tokens,reason,count", [([8, 9, 2, 3], "eos", 2), ([8, 9, 3], "eos", 2),
                                              ([8, 9, 10, 11], "length", 4), ([8, 9], "unknown", 2)])
def test_generation_observer_tracks_actual_outer_default_and_prompt_offset(tokens, reason, count):
    model = Model(tokens)
    observer = GenerationObserver(model)
    result = model.generate(input_ids=torch.tensor([[2, 20]]), max_new_tokens=4)
    assert result.sequences.tolist() == [[2, 20] + tokens]
    assert observer.records == [{"generated_tokens": count, "max_new_tokens": 4,
                                 "finish_reason": reason, "truncated": reason == "length"}]
    observer.close()
    observer.close()
    assert "generate" not in vars(model)


def test_generation_override_and_existing_method_are_restored():
    model = Model([8, 7])
    old_method = model.generate
    model.generate = old_method
    observer = GenerationObserver(model)
    model.generate(input_ids=torch.tensor([[1]]), max_new_tokens=2, eos_token_id=7)
    assert observer.records[0]["finish_reason"] == "eos"
    observer.close()
    assert model.generate is old_method


def test_closed_observer_does_not_retain_model():
    model = Model([2])
    reference = weakref.ref(model)
    observer = GenerationObserver(model)
    observer.close()
    del model
    gc.collect()
    assert reference() is None
