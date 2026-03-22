# PRD: community-1 Diarization Model Packaging and Anonymous Distribution

## 1. Background

AudioAssist currently uses `pyannote-diarization-community-1` as the default diarization model in code and documentation.

Current implementation assumptions:

- The diarizer is auto-downloaded on first use
- The app loads diarization models from a local folder via `Pipeline.from_pretrained(local_path)`
- The current model catalog references:
  - `pyannote/speaker-diarization-community-1`

Recent investigation identified an alternative public repository:

- `pyannote-community/speaker-diarization-community-1`

This repository appears to:

- expose files publicly
- allow direct anonymous download via `resolve/main/...`
- contain a small model footprint
- avoid the earlier Hugging Face gated-access friction

This creates an opportunity to simplify first-run experience and reduce user setup friction.

## 2. Problem Statement

The current first-run diarization flow is not ideal:

- users may hit model download friction
- repository assumptions are outdated
- current repo reference may not match the best publicly accessible source
- packaging and offline-readiness are harder than necessary

The team needs a clear product decision on:

1. whether the `pyannote-community` repository can be used as the canonical source
2. whether the model can be anonymously downloaded at runtime
3. whether the model should be bundled in the app package
4. which exact files are required for successful local loading

## 3. Goals

### 3.1 Primary Goals

1. Standardize the default diarization source on an anonymously accessible community repository
2. Make first-run diarization work without requiring Hugging Face token setup
3. Support future bundling of the model inside the app package
4. Keep runtime loading compatible with the current `Pipeline.from_pretrained(local_path)` architecture

### 3.2 Non-Goals

- Changing diarization model family
- Replacing pyannote.audio
- Building a custom diarization pipeline
- Supporting all pyannote repositories generically

## 4. Current State

Relevant current code:

- [src/model_manager.py](/Users/feifei/programing/audioassist/audioassist-researcher/src/model_manager.py)
- [src/diarize.py](/Users/feifei/programing/audioassist/audioassist-researcher/src/diarize.py)
- [src/pipeline.py](/Users/feifei/programing/audioassist/audioassist-researcher/src/pipeline.py)

Current behavior:

- `ModelManager` stores metadata for the diarizer
- `pipeline.run(...)` ensures the diarizer model is present
- `DiarizationEngine.load()` downloads if needed, then loads from local path
- the diarizer local path is treated as a folder-based model root

The current model ID maps to:

- `pyannote/speaker-diarization-community-1`

## 5. Investigation Summary

### 5.1 Public Repository

The repository:

- `https://huggingface.co/pyannote-community/speaker-diarization-community-1`

appears to be publicly accessible, with visible file listing and no gating wall.

Observed public file list includes:

- `config.yaml`
- `embedding/pytorch_model.bin`
- `plda/plda.npz`
- `plda/xvec_transform.npz`
- `segmentation/pytorch_model.bin`

The repository file listing shows a total size around `33.7 MB`, which is dramatically smaller than previous rough estimates.

### 5.2 Runtime Dependency Structure

From `config.yaml`, the diarization pipeline depends on:

- `segmentation`
- `embedding`
- `plda`

The `config.yaml` points to:

- `$model/segmentation`
- `$model/embedding`
- `$model/plda`

Therefore, the minimum runtime directory appears to be:

```text
<model_root>/
  config.yaml
  embedding/
    pytorch_model.bin
  plda/
    plda.npz
    xvec_transform.npz
  segmentation/
    pytorch_model.bin
```

### 5.3 Compatibility with Current Project

The current project loads the diarizer by local folder path through:

- `Pipeline.from_pretrained(local_path)`

The current `ModelManager` only checks for `config.yaml` existence to validate a diarizer folder.

This means the current architecture is already compatible with a preloaded local folder, provided the directory contains the required child paths referenced by `config.yaml`.

## 6. Product Decision

### 6.1 Recommended Decision

The project should adopt:

- `pyannote-community/speaker-diarization-community-1`

as the default community diarization source for the `community-1` model.

### 6.2 Runtime Policy

Short term:

- support anonymous runtime download from the public repository

Medium term:

- support bundling the model into the packaged app for zero-friction first run

## 7. Feature Requirements

### F1. Anonymous Download Support

The app should be able to fetch the model without HF token setup when using the community repository.

Expected behavior:

- if local diarizer model is missing
- and community source is selected
- the model files are downloaded anonymously
- the model is placed under the expected local model root

### F2. Local Folder Compatibility

The app must support loading the diarizer from a fully local directory without network access.

Expected behavior:

- if the model folder already exists locally
- `Pipeline.from_pretrained(local_path)` should run successfully

### F3. Bundling Support

The app packaging workflow should support optionally embedding the community diarizer inside the shipped product.

Expected behavior:

- packaged app can include the model under local models directory
- first run does not require network or token for diarization

### F4. Catalog Update

The model catalog should be updated so the default community diarizer points to the new repository source.

Expected behavior:

- `repo_id` is updated
- model metadata reflects current reality

### F5. Documentation Update

README and product notes should clearly state:

- the default community diarizer source
- whether it is built-in or anonymously downloaded
- that HF token is not needed for this path

## 8. Functional Design

### 8.1 Required Model Files

The minimum required file set for `community-1` should be treated as:

- `config.yaml`
- `embedding/pytorch_model.bin`
- `plda/plda.npz`
- `plda/xvec_transform.npz`
- `segmentation/pytorch_model.bin`

Other repository files such as:

- `.gitattributes`
- `README.md`
- `diarization.gif`
- `embedding/README.md`
- `plda/README.md`

are not required for inference.

### 8.2 Local Directory Layout

Recommended local storage:

```text
models/
  pyannote-diarization-community-1/
    config.yaml
    embedding/
      pytorch_model.bin
    plda/
      plda.npz
      xvec_transform.npz
    segmentation/
      pytorch_model.bin
```

This layout is compatible with the current local-path loading approach.

## 9. Engineering Changes Required

### 9.1 Model Catalog

Update [src/model_manager.py](/Users/feifei/programing/audioassist/audioassist-researcher/src/model_manager.py):

- change community diarizer `repo_id` from:
  - `pyannote/speaker-diarization-community-1`
- to:
  - `pyannote-community/speaker-diarization-community-1`

### 9.2 Model Size Metadata

The current `size_gb=0.5` for the community diarizer is not aligned with the observed public repository footprint.

Recommendation:

- update the value to reflect current packaged/downloaded reality
- or rename semantics in UI/docs to indicate it is an estimated footprint rather than exact file sum

### 9.3 Validation Logic

The current `_has_key_files()` logic only checks `config.yaml` for diarizers.

Recommendation:

- strengthen diarizer validation to confirm:
  - `config.yaml`
  - `embedding/pytorch_model.bin`
  - `plda/plda.npz`
  - `plda/xvec_transform.npz`
  - `segmentation/pytorch_model.bin`

This avoids false positives from partial downloads.

### 9.4 Packaging Integration

When packaging the app:

- copy the full community diarizer folder into the shipped model root
- ensure runtime model lookup checks packaged model location first

## 10. UX and Product Impact

### 10.1 User Experience Improvement

This change improves the default experience by removing:

- Hugging Face account creation
- token configuration
- gated model confusion

It supports a much simpler user promise:

- diarization works out of the box

### 10.2 Packaging Tradeoff

Because the model footprint is small, bundling it has limited package-size impact compared with ASR models.

This makes `community-1` a strong candidate for pre-bundling.

## 11. Risks

### 11.1 Repository Stability

The `pyannote-community` repository appears suitable now, but the team should avoid hard-coding assumptions without basic fallback handling.

Mitigation:

- version and test the download path
- fail clearly if required files are missing

### 11.2 Partial Download Risk

If files are downloaded individually or packaging is incomplete, `Pipeline.from_pretrained(local_path)` may fail at runtime.

Mitigation:

- validate the full required file set before marking the model as installed

### 11.3 Documentation Drift

Current code/docs still refer to the older source and historical token assumptions.

Mitigation:

- update README
- update catalog metadata
- update packaging notes

## 12. Rollout Plan

### Phase 1

- switch catalog repo ID
- update validation logic
- validate the minimal file set locally

### Phase 2

- update docs and packaging notes
- support deterministic direct download of the required files

### Phase 3

- optionally bundle the community diarizer into packaged desktop builds

## 13. Success Criteria

This effort is successful if:

1. `community-1` can be loaded successfully from a local folder containing the required file set
2. users do not need HF token setup for default diarization
3. runtime auto-download succeeds anonymously from the selected repository
4. packaged builds can optionally ship with the community diarizer preinstalled
5. README and model metadata match actual behavior

## 14. Final Recommendation

Adopt `pyannote-community/speaker-diarization-community-1` as the default source for the `community-1` diarizer.

Treat the following as the minimum required inference set:

- `config.yaml`
- `embedding/pytorch_model.bin`
- `plda/plda.npz`
- `plda/xvec_transform.npz`
- `segmentation/pytorch_model.bin`

Update the project so that:

- the model can be anonymously downloaded
- the same directory structure can be packaged into the app
- local validation is stricter than the current single-file check
