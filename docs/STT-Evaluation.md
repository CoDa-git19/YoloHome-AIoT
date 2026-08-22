# STT Evaluation — PhoWhisper vs faster-whisper

**Measured:** 2026-08-22 · Windows, CPU-only, Python 3.14
**Checkpoint:** `finetune_final` (PhoWhisper-base fine-tuned on Vietnamese smart-home commands)
**Command:** `python -m modules.speech_recognition.evaluate_model --engine <engine>`

---

## 1. Results

| Metric | PhoWhisper (transformers) | faster-whisper (CTranslate2, int8) |
|---|---|---|
| Average WER | **0.000** | 0.194 |
| Average CER | **0.000** | 0.139 |
| Exact match rate | **100%** | 33.3% |
| Average latency | 1.290 s | **0.771 s** |
| Samples | 3 | 3 |

## 2. Per-sample output

| Ground truth | PhoWhisper | faster-whisper |
|---|---|---|
| bật đèn phòng khách | ✓ | `ật đèn phòng khách` |
| tắt đèn nhà vệ sinh | ✓ | ✓ |
| tắt quạt đi | ✓ | `quạt đi` |

## 3. Finding: faster-whisper drops leading syllables

faster-whisper is roughly **1.7× faster** but loses the first syllable on two of three samples.

The variables were controlled: same audio files, same checkpoint, same tokenizer (SHA256 verified identical between `finetune_final/` and `finetune_final_ct2/`). The only difference is the inference backend and its int8 quantisation.

Vietnamese stop consonants (`b`, `t`) are short and carry little energy. Quantising weights from float32 to int8 most plausibly costs resolution on
exactly this kind of weak signal. Note this is a hypothesis consistent with the evidence, not a verified cause — isolating it would require re-running the CT2 model at `compute_type="float32"`.

`ật đèn phòng khách` is the informative case: the `/b/` was **dropped**, not misheard as another word. That points to signal loss rather than a semantic
error.

## 4. Decision: keep `STT_ENGINE=phowhisper` as the default

Half a second is not worth losing the control verb. `tắt quạt đi` → `quạt đi` strips the action entirely, leaving the LLM with a device and no operation. For
a system whose whole purpose is executing device commands, a dropped verb is a far worse failure than a slower response.

faster-whisper stays available behind `STT_ENGINE=faster-whisper` for deployment on weaker hardware, where the latency trade-off may be worth revisiting — ideally with `compute_type="float32"` measured first.

## 5. Limits of this measurement

- **n = 3 is too small to conclude anything.** A single error moves WER between 0.08 and 0.19. Around 15–20 samples would be needed for the numbers to hold up.
- One speaker, quiet room, laptop microphone. 
- No noisy samples, no silence samples, no fast-speech samples. The `category` field on `TestSample` exists for this but is currently unused.
- Both engines ran on the same machine back to back, so latency figures are comparable to each other but not to any other hardware.

## 6. Method notes

Three corrections were applied. Without them the numbers are materially wrong, and the first two flattered nobody — they made both engines look worse.

**Warm-up pass.** Models load lazily on the first `transcribe()` call, so sample one absorbed the entire load time (10.8 s instead of 1.3 s). Leaving it in
inflated reported latency by roughly 3.5×. `run_evaluation()` now transcribes one sample before the timed loop begins.

**Punctuation normalisation.** STT deliberately preserves punctuation (`docs/Integration-Contracts.md`, Contract A), but the consumer is the LLM, which
does not care about a trailing period. Before normalising, `tắt quạt đi.` scored WER 0.333 against `tắt quạt đi` — measuring the grader's strictness, not the
model's accuracy.

**Re-recorded the audio.** The first take clipped the opening syllable because `record_wav_bytes()` starts capturing immediately, so **both** engines misheard `bật` as `hoặc`. Two independent models failing identically on one file is a signal about the recording, not the models.

That last point is not only a test-harness issue. `system_core/main.py` calls the same `record_wav_bytes()` with no lead-in and no cue to the user, so a real user who presses the button and then draws breath will lose the same syllable. Two possible fixes, in increasing order of effort:

1. Print a `>>> SPEAK NOW <<<` prompt before `sd.rec()` and add ~0.5 s of padding 
2. Use voice-activity detection to trim leading and trailing silence (`faster-whisper` already exposes `vad_filter=True`)

## 7. Reproducing

```bash
# Record the samples first — .wav files are excluded by .gitignore
# and are not in the repository.
python -m modules.speech_recognition.evaluate_model --engine phowhisper
python -m modules.speech_recognition.evaluate_model --engine faster-whisper
```

Ground truth for the three samples lives in `TEST_SET` inside
`modules/speech_recognition/evaluate_model.py`.