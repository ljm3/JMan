# Meeting Scribe

A **self-contained desktop application** that turns a meeting recording into
polished documents — entirely on your own computer. No audio, transcript or text
ever leaves the machine.

It does, end to end:

1. **Asks where the audio is** — a local file *or* an online link (direct media
   URL, YouTube, Google Drive, Dropbox, SharePoint, podcast page, … via yt-dlp).
2. **Asks which documents to produce** (any combination):
   * digital transcript that **highlights words as the audio plays** (interactive HTML)
   * printed transcript with **identified** speaker attribution (real names)
   * printed transcript with **generic** speaker attribution (Speaker 1, Speaker 2, …)
   * **minutes with synopsis**
   * **minutes without synopsis**
   * **action items**
3. **Asks whether the deliverables should give access to the source recording** —
   no / attach a copy / link to it.
4. **Asks the output formats** — DOCX, ODT, PDF (choose one or more).
5. **Analyses the audio**: transcription → word alignment → speaker diarization →
   minutes / synopsis / action-item extraction, running only the steps the
   selected documents need.
6. **Runs multiple accuracy checks** before writing anything — transcription
   confidence, diarization status, unnamed speakers, action items missing
   owners / due dates / sources, empty minutes sections, plus an LLM reviewer
   pass — and writes an **Accuracy Report** alongside the documents.
7. **Writes every deliverable into the same folder as the audio file** (for a
   downloaded URL, into the download folder).
8. **Tells you it finished** and lists the files, with buttons to open the
   folder, the interactive transcript and the session log.
9. **Shows its work and keeps logs**: a live console during processing, and a
   **numbered, dated log file per session** in `…\MeetingScribe\logs`
   (`session-0001_2026-08-29.log`, `session-0002_…`, …) that records your
   answers; every line is also appended to a running `history.log`.

## The model stack

| Function | Component |
| --- | --- |
| Audio transcription | **faster-whisper** (CTranslate2) — `large-v3` on GPU, `medium`/`distil-large-v3` on CPU |
| Word-level timing | **torchaudio** CTC **forced alignment** (bundled weights, no token) |
| Speaker attribution (generic + named) | **pyannote.audio** `speaker-diarization-3.1`, names confirmed by you afterwards |
| Minutes / synopsis / action items | **Qwen3 Instruct** via Hugging Face **transformers** (`4B` / `8B` / `14B` / `32B`) |
| Accuracy checks | deterministic application rules **+** a Qwen3 reviewer pass |

> WhisperX is *not* a dependency — its transcribe + align + diarize orchestration
> is reproduced here directly (faster-whisper + torchaudio alignment +
> pyannote + overlap-based speaker assignment) to avoid its version pinning.

## Requirements

* Windows 10/11, 64-bit
* ~15 GB free disk for models (downloaded on first use, into the workspace)
* 16 GB RAM minimum. **CPU-only is supported and is the default tuning.**
  * On a 16 GB CPU box the installer defaults to Whisper `medium` + **Qwen3-4B**.
  * Qwen3-8B needs ~32 GB RAM on CPU and is noticeably slower; change it in
    *File ▸ Settings*.
* A **Hugging Face token** for speaker separation. Without it the app still runs
  but attributes everything to a single speaker.

## Install

```powershell
cd C:\Users\<you>\.local\bin\meeting-scribe
.\setup.ps1
```

The script finds or installs Python 3.12, creates `.\.venv`, installs
`requirements.txt` then `requirements-ml.txt`, and writes hardware-tuned
defaults. Options: `-Recreate`, `-SkipMl`, `-DownloadModels`.

### Enable speaker separation (optional)

1. Create a token at <https://huggingface.co/settings/tokens>.
2. Accept the conditions on **both** model pages while logged in:
   * <https://huggingface.co/pyannote/speaker-diarization-3.1>
   * <https://huggingface.co/pyannote/segmentation-3.0>
3. Paste the token into *File ▸ Settings ▸ Hugging Face token*.

## Run

```powershell
.\run.ps1                     # desktop app
.\run.ps1 --doctor            # environment diagnostics
.\run.ps1 --cli --source "C:\path\meeting.m4a" `
          --docs transcript_named,minutes_with_synopsis,action_items `
          --formats docx,pdf --audio-access link --speakers 3
```

## Where things go

```
%APPDATA%\MeetingScribe\config.toml         settings
%USERPROFILE%\MeetingScribe\
    logs\session-XXXX_YYYY-MM-DD.log        one per launch (+ history.log)
    downloads\                              audio fetched from URLs
    models\                                 local model cache (HF_HOME)
    projects\*.mscribe.json                 full analysis state / checkpoints
<folder of the audio file>\
    <name> - Transcript (named speakers).docx
    <name> - Minutes.docx
    <name> - Action Items.pdf
    <name> - Accuracy Report.docx
    <name> - Interactive Transcript.html   + <name>_media\<audio>
```

## Notes on accuracy

* Nothing is written until the accuracy checks have run; the results screen and
  the *Accuracy Report* list every error/warning with a suggested fix.
* "Identified speaker attribution" opens a **speaker-confirmation dialog** after
  analysis (with audio samples) so you verify which name maps to which voice,
  then re-exports.
* Low-confidence transcript passages are flagged with timestamps for review.
