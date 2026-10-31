import base64
import json
import os
import time
import requests
import tkinter as tk
from tkinter import ttk, messagebox
from urllib.parse import urlparse
from google import genai
from google.genai import types
from google.genai.errors import APIError
from gooey import Gooey, GooeyParser

def format_time(seconds):
    """Convert seconds into a clean MM:SS format."""
    if seconds is None:
        return "00:00"
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{mins:02d}:{secs:02d}"

class SpeakerRenameDialog:
    """GUI dialog to display speaker samples and request custom names."""
    def __init__(self, speaker_samples):
        self.speaker_samples = speaker_samples
        self.mapping = {}
        
        self.root = tk.Tk()
        self.root.title("Rename Speakers")
        self.root.geometry("700x600")
        self.root.minsize(500, 400)
        self.root.attributes("-topmost", True)
        
        main_frame = ttk.Frame(self.root, padding=15)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        title_label = ttk.Label(main_frame, text="Assign Names to Detected Speakers", font=("Arial", 12, "bold"))
        title_label.pack(anchor=tk.W, pady=(0, 5))
        
        desc_label = ttk.Label(main_frame, text="Review sample entries for each speaker below and enter their desired names:", wraplength=640)
        desc_label.pack(anchor=tk.W, pady=(0, 10))
        
        # Dedicated container frame to isolate the canvas and scrollbar
        canvas_container = ttk.Frame(main_frame)
        canvas_container.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(0, 10))
        
        canvas = tk.Canvas(canvas_container, highlightthickness=0)
        scrollbar = ttk.Scrollbar(canvas_container, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas_window = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        canvas.bind(
            "<Configure>",
            lambda e: canvas.itemconfig(canvas_window, width=e.width)
        )
        
        # Mousewheel scrolling bindings across platforms
        def _on_mousewheel(event):
            if event.delta:
                canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            else:
                if event.num == 4:
                    canvas.yview_scroll(-1, "units")
                elif event.num == 5:
                    canvas.yview_scroll(1, "units")
                    
        canvas.bind_all("<MouseWheel>", _on_mousewheel)
        canvas.bind_all("<Button-4>", _on_mousewheel)
        canvas.bind_all("<Button-5>", _on_mousewheel)
        
        # Pack canvas and scrollbar inside their container frame
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.entries = {}
        
        for speaker, samples in speaker_samples.items():
            frame = ttk.LabelFrame(scrollable_frame, text=f" {speaker} ", padding=10)
            frame.pack(fill=tk.X, expand=True, pady=6, padx=5)
            
            formatted_samples = []
            for s in samples:
                time_str = format_time(s['start'])
                text = s['text'].strip()
                # Limit each sample segment to approximately two lines (max ~115 chars)
                if len(text) > 115:
                    text = text[:112].rsplit(' ', 1)[0] + "..."
                formatted_samples.append(f"• [{time_str}] {text}")
                
            samples_text = "\n".join(formatted_samples)
            lbl = ttk.Label(frame, text=samples_text, wraplength=600, justify=tk.LEFT)
            lbl.pack(anchor=tk.W, pady=(0, 6))
            
            entry_frame = ttk.Frame(frame)
            entry_frame.pack(fill=tk.X, anchor=tk.W)
            
            ttk.Label(entry_frame, text="New Name: ").pack(side=tk.LEFT)
            entry = ttk.Entry(entry_frame, width=35)
            entry.insert(0, speaker)
            entry.pack(side=tk.LEFT, padx=5)
            
            self.entries[speaker] = entry
            
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=(10, 0))
        
        save_btn = ttk.Button(btn_frame, text="Save Names & Process Transcript", command=self.on_save)
        save_btn.pack(side=tk.RIGHT)
        
    def on_save(self):
        try:
            self.root.unbind_all("<MouseWheel>")
            self.root.unbind_all("<Button-4>")
            self.root.unbind_all("<Button-5>")
        except Exception:
            pass
        for speaker, entry in self.entries.items():
            new_name = entry.get().strip()
            self.mapping[speaker] = new_name if new_name else speaker
        self.root.destroy()
        
    def show(self):
        self.root.mainloop()
        return self.mapping

def process_omi_job_flow(audio_path, omi_api_key, max_speakers=4):
    """Executes the full Omi presigned upload, job completion, and long-polling workflow."""
    api = "https://api.omi.health"
    headers = {"Authorization": f"Bearer {omi_api_key}"}
    filename = os.path.basename(audio_path)
    file_size = os.path.getsize(audio_path)
    
    # Infer basic mime type
    ext = os.path.splitext(audio_path)[1].lstrip('.').lower()
    content_type_map = {
        "mp3": "audio/mpeg",
        "wav": "audio/wav",
        "flac": "audio/flac",
        "m4a": "audio/mp4"
    }
    content_type = content_type_map.get(ext, "audio/mpeg")

    # 1. Create a Job & Reserve Slot
    print(f"Creating Omi transcription job for {filename} (max_speakers={max_speakers})...")
    create_resp = requests.post(
        f"{api}/v1/jobs",
        headers=headers,
        json={
            "model": "omi-medical-1",
            "filename": filename,
            "content_type": content_type,
            "content_length_bytes": file_size,
            "language": "en",
            "response_format": "verbose_json",
            "diarize": True,
            "max_speakers": max_speakers
        }
    )
    create_resp.raise_for_status()
    job = create_resp.json()

    # 2. Upload directly to signed storage
    print("Uploading audio directly to signed storage...")
    with open(audio_path, "rb") as audio_file:
        upload_resp = requests.post(
            job["upload"]["url"],
            data=job["upload"]["fields"],
            files={"file": (filename, audio_file, content_type)}
        )
    upload_resp.raise_for_status()

    # 3. Freeze upload and enqueue job
    print("Enqueuing transcription job...")
    requests.post(job["complete_url"], headers=headers).raise_for_status()

    # 4. Long-poll server-side
    print("Polling job status (server-side long poll)...")
    while True:
        state_resp = requests.get(
            job["poll_url"],
            headers=headers,
            params={"wait": 20, "include_result": "true"},
            timeout=30
        )
        state_resp.raise_for_status()
        state = state_resp.json()
        
        status = state.get("status")
        print(f"Current job status: {status}")

        if status == "running":
            partial = (state.get("transcription") or {}).get("partial_result_url")
            if partial:
                try:
                    preview = requests.get(partial, headers=headers).json()
                    text_preview = preview.get("text", "")[:80]
                except Exception:
                    pass

        elif status == "succeeded":
            result_envelope = state.get("result", {})
            result = result_envelope.get("content")
            
            if result is None and "download_url" in result_envelope:
                result_url = result_envelope["download_url"]
                result_host = (urlparse(result_url).hostname or "").lower()
                result_headers = headers if result_host in {"api.omi.health", "api.eu.omi.health"} else {}
                
                res_resp = requests.get(result_url, headers=result_headers)
                res_resp.raise_for_status()
                result = res_resp.json()

            # Speaker enrichment lifecycle check
            decoration_status = (result.get("decoration") or {}).get("status")
            if decoration_status == "processing":
                print("Text final; awaiting speaker label enrichment...")
                time.sleep(1.0)
                continue
            
            return result

        elif status == "failed":
            error_msg = state.get("error", {}).get("message", "Unknown job error")
            raise RuntimeError(f"Omi job failed: {error_msg}")

def extract_turns_from_omi(result):
    """Extract speaker turns using Omi's canonical segment or word structure."""
    turns = []
    
    # Check segments array (Primary readable turn structure)
    segments = result.get("segments") or []
    if segments:
        for seg in segments:
            speaker_raw = seg.get("speaker") or "0"
            speaker_str = str(speaker_raw).strip()
            
            if speaker_str.lower().startswith("speaker"):
                speaker_label = speaker_str.replace("_", " ").title()
            else:
                speaker_label = f"Speaker {speaker_str}"
                
            text = seg.get("text", "").strip()
            start = seg.get("start", 0.0)
            
            if text:
                turns.append({
                    "speaker": speaker_label,
                    "start": start,
                    "text": text
                })
        return turns

    # Fallback: Aggregate word array if segments are unavailable
    words = result.get("words") or []
    if words:
        current_speaker = None
        current_sentence = []
        turn_start_time = 0.0
        
        for w in words:
            speaker_raw = w.get("speaker") or "0"
            text = w.get("word") or w.get("text") or ""
            word_start = w.get("start", 0.0)
            
            speaker_str = str(speaker_raw).strip()
            if speaker_str.lower().startswith("speaker"):
                speaker_label = speaker_str.replace("_", " ").title()
            else:
                speaker_label = f"Speaker {speaker_str}"
            
            if speaker_label != current_speaker:
                if current_sentence:
                    turns.append({
                        "speaker": current_speaker,
                        "start": turn_start_time,
                        "text": ' '.join(current_sentence).strip()
                    })
                    current_sentence = []
                current_speaker = speaker_label
                turn_start_time = word_start
            
            current_sentence.append(text)
            
        if current_sentence:
            turns.append({
                "speaker": current_speaker,
                "start": turn_start_time,
                "text": ' '.join(current_sentence).strip()
            })
            
    return turns

def generate_with_fallback(api_key, contents, system_instruction=None):
    """Initialize client with retries and run sequential model fallback cascade."""
    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(
                attempts=3,
                initial_delay=2.0,
                max_delay=10.0
            )
        )
    )

    model_cascade = [
        "gemini-3.8-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-2.5-flash"
    ]
    
    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)
    )

    for model in model_cascade:
        try:
            print(f"Attempting generation with: {model}...")
            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=config
            )
            print(f"Success using {model}!")
            
            usage_metadata = getattr(response, "usage_metadata", None)
            input_tokens = getattr(usage_metadata, "prompt_token_count", None) if usage_metadata else None
            output_tokens = getattr(usage_metadata, "candidates_token_count", None) if usage_metadata else None
            total_tokens = getattr(usage_metadata, "total_token_count", None) if usage_metadata else None

            stats = {
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens
            }

            return response.text, stats
            
        except APIError as e:
            if e.code in [429, 500, 502, 503, 504]:
                print(f"⚠️ {model} failed with HTTP {e.code}. Cascading to next model...")
                time.sleep(1)
                continue
            else:
                print(f"❌ Permanent error with {model}: {e.message}")
                raise e
                
    raise RuntimeError("All Gemini models in the fallback cascade failed due to high demand.")

@Gooey(
    program_name="Omi Medical Transcriber",
    program_description="Transcribe medical audio directly with Omi-Medical-1 & Gemini Clinical Cleanup"
)
def main():
    parser = GooeyParser(description="Select an audio file and output path for transcription.")
    
    parser.add_argument(
        "audio_file",
        help="Select the audio file (MP3, WAV, FLAC, M4A) to transcribe",
        widget="FileChooser",
        gooey_options={"wildcard": "Audio Files (*.mp3;*.wav;*.flac;*.m4a)|*.mp3;*.wav;*.flac;*.m4a|All files (*.*)|*.*"}
    )
    
    parser.add_argument(
        "output_file",
        help="Select or enter the path for the output transcription markdown file",
        widget="FileSaver",
        gooey_options={"wildcard": "Markdown Files (*.md)|*.md|Text Files (*.txt)|*.*"}
    )
    
    parser.add_argument(
        "--omi-api-key",
        help="Omi API Key (Optional if OMI_API_KEY env var is already set)",
        widget="PasswordField",
        default=""
    )

    parser.add_argument(
        "--max-speakers",
        help="Maximum number of speakers expected (cannot exceed 4)",
        widget="IntegerField",
        default=4,
        gooey_options={
            "min": 1,
            "max": 4,
            "increment": 1
        }
    )
    
    args = parser.parse_args()
    audio_path = args.audio_file
    output_path = args.output_file

    if not os.path.splitext(output_path)[1]:
        output_path += ".md"

    omi_api_key = args.omi_api_key.strip() or os.getenv("OMI_API_KEY")
    if not omi_api_key:
        raise ValueError(
            "Omi API key missing! Either set your OMI_API_KEY environment variable "
            "or paste your key into the Omi API Key field in the Gooey window."
        )

    gemini_api_key = os.getenv("GEMINI_API_KEY")
    if not gemini_api_key:
        raise ValueError("GEMINI_API_KEY environment variable is missing! Please set it before running.")

    max_speakers = int(args.max_speakers)
    if max_speakers > 4:
        max_speakers = 4
    if max_speakers < 1:
        max_speakers = 1

    # Process audio via Omi's presigned upload & long-poll job pipeline
    result = process_omi_job_flow(audio_path, omi_api_key, max_speakers=max_speakers)

    # Extract speaker turns
    turns = extract_turns_from_omi(result)
    
    if turns:
        speaker_samples = {}
        for turn in turns:
            spk = turn["speaker"]
            text = turn["text"]
            if len(text.split()) >= 10:
                if spk not in speaker_samples:
                    speaker_samples[spk] = []
                if len(speaker_samples[spk]) < 5:
                    speaker_samples[spk].append({
                        "start": turn["start"],
                        "text": text
                    })

        print("Launching GUI to rename speakers...")
        dialog = SpeakerRenameDialog(speaker_samples)
        speaker_mapping = dialog.show()

        markdown_lines = []
        for turn in turns:
            orig_speaker = turn["speaker"]
            final_speaker = speaker_mapping.get(orig_speaker, orig_speaker).upper()
            time_str = format_time(turn["start"])
            
            line = f"**{final_speaker}** [{time_str}]\n\n{turn['text']}\n"
            markdown_lines.append(line)

        raw_markdown_transcript = "\n".join(markdown_lines)

        system_instruction = (
            r"""You are an expert medical transcriptionist and clinical editor. Your task is to clean up the provided medical visit transcript while maintaining 100% clinical accuracy and medical integrity.

### Execution Instructions (Follow in Order):

1. **Speaker Identification ("SPEAKER 0"):**
   - Identify "SPEAKER 0" using conversational context and change the label to the correct speaker 

2. **Merge Consecutive Turns (CRITICAL):**
   - If two or more consecutive turns belong to the same speaker (after resolving "SPEAKER 0" labels), you MUST merge them into a single speaker block under the initial timestamp. Do not leave separate consecutive blocks for the same person.

3. **Remove Conversational Fillers & Disfluencies:**
   - Strip out vocal tics, crutch words, non-lexical sounds ("um," "uh," etc.), stutters, false starts, and duplicate words.

4. **Preserve Medical Integrity & Accuracy:**
   - Retain exact numbers, measurements, frequency, severity statements, dosages, medication names, and provider instructions without alteration.

5. **Format & Timestamps (CRITICAL):**
   - Retain speaker labels and timestamps exactly as: `**SPEAKER NAME** [MM:SS]`
   - **Newline Enforcement:** Add \ and newline immediately after the closing bracket to force text onto a new line (e.g., `**SPEAKER NAME** [MM:SS]\\n`).
   - Leave a blank line separating each speaker turn.

6. **Output Requirements:**
   - Output ONLY the cleaned Markdown transcript

### Example of Expected Formatting & Merging:
Input:
**ELAINE** [00:02] And so I went to ER.
**ELAINE** [00:07] And they sent me home.

Output:
**ELAINE** [00:02]\
 And so I went to ER. And they sent me home.""")
        

        contents = f"### Raw Transcript:\n{raw_markdown_transcript}"


        print("Sending draft markdown transcript through Gemini fallback cascade for clinical cleanup...")
        final_markdown_transcript, gemini_stats = generate_with_fallback(
            api_key=gemini_api_key,
            contents=contents,
            system_instruction=system_instruction
        )

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(final_markdown_transcript)
        
        print(f"\nPolished transcript successfully saved to: {output_path}")

    else:
        print("\nWarning: Could not extract turns from Omi response. Saving raw JSON...")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("```json\n" + json.dumps(result, indent=2) + "\n```")
        print(f"Full response JSON saved to: {output_path}")
        gemini_stats = None

    audio_seconds = result.get("duration")

    print(f"\n--- Request & Model Run Statistics ---")
    if audio_seconds is not None:
        print(f"Audio Duration:     {audio_seconds} seconds")
        
    if gemini_stats:
        print(f"Gemini Model Used:  {gemini_stats['model']}")
        print(f"Gemini Input Tokens:  {gemini_stats['input_tokens']}")
        print(f"Gemini Output Tokens: {gemini_stats['output_tokens']}")
        print(f"Gemini Total Tokens:  {gemini_stats['total_tokens']}")


if __name__ == "__main__":
    main()