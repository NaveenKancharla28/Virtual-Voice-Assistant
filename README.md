# Virtual Voice Assistant

A browser-based virtual voice assistant that streams microphone audio from the browser to a Python FastAPI backend which forwards audio/text to a realtime LLM service (OpenAI Realtime). The backend returns streaming text and raw PCM16 audio deltas that the frontend plays in the browser.

This README documents architecture, required environment variables, how to run locally, deployment notes (Fly), security and secrets handling, WebSocket message expectations, audio formats, troubleshooting and recommended next steps.

---

## Repository layout

- `index.html` - static frontend UI and client-side WebSocket bridge.
- `setup.py` - FastAPI backend, OpenAI realtime WebSocket bridge, mic streaming, startup/shutdown hooks.
- `amadeus_api.py` - optional helper to call Amadeus hotel API (tool integration used by the assistant).
- `Procfile` - single `web` entry for hosting platforms using a process file.
- `dockerfile` - container image recipe used by CI/build systems or manual Docker builds.
- `requirements.txt` - Python dependencies used by the backend.
- `.gitignore` - recommended to ignore `.env`, virtual environments, and build artifacts.

---

## High level architecture

1. Browser loads `index.html` and constructs a `VirtualAssistant` client.
2. Browser opens a WebSocket to the backend at `/ws`.
3. When the user starts recording, the frontend sends a control message (`input_audio_buffer.append` with a base64 payload marker such as `start_recording`) to start the server-side recording state. The frontend then streams base64-encoded PCM16 audio blocks to the backend.
4. The backend maintains a background connection to the OpenAI realtime WebSocket. It forwards valid audio messages to OpenAI and receives streaming `response.text.delta` and `response.audio.delta` events.
5. `response.text.delta` is forwarded to the frontend as text updates; `response.audio.delta` is base64-encoded PCM16 audio which the frontend decodes and plays using the Web Audio API.

---

## Requirements

- Python 3.10+ (3.11 recommended) with a virtual environment.
- The frontend is static HTML/JS and requires a modern browser with Web Audio support.
- For local server-side audio support only (optional), `pyaudio` is used. Note: `pyaudio` has native dependencies and can fail to build in container environments; see Deployment notes.

---

## Environment variables

- `OPENAI_API_KEY` (required) - OpenAI API key with access to the realtime API.
- `AMADEUS_CLIENT_ID` and `AMADEUS_CLIENT_SECRET` (optional) - required only if you use `amadeus_api.py` and the `search_hotels` tool.
- `PORT` (optional) - port the server binds to locally; defaults to `8080`. Platforms like Fly set this automatically.

Important: Do not commit keys or credentials. Use a secret manager or the platform's secrets mechanism.

---

## Local development

1. Create and activate a virtual environment:

```bash
python -m venv venv
source venv/bin/activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Set required environment variables (example):

```bash
export OPENAI_API_KEY="sk-..."
# Optional: export AMADEUS_CLIENT_ID=... and AMADEUS_CLIENT_SECRET=...
```

4. Start the server (development):

```bash
# Recommended: run with uvicorn
PORT=8080 uvicorn setup:app --host 0.0.0.0 --port 8080
```

5. Open a browser on `http://localhost:8080/` and use the UI. Click "Start Listening" to ensure the browser resumes the `AudioContext` and the app will send the appropriate control messages to the backend.

Notes:
- The backend `startup` hook attempts to start the OpenAI realtime websocket and spawn the mic streaming thread. If `pyaudio` can't open a device, the backend will continue to run but server-side audio playback will not be available.

---

## WebSocket message expectations (frontend <-> backend)

- Frontend -> Backend control messages:
  - `{"type":"input_audio_buffer.append","audio":"<base64>"}`
    - Special control markers used by this project: base64("start_recording"), base64("stop_recording"). The backend handles these locally and does not forward them to OpenAI.

- Backend -> Frontend events (examples):
  - `{"type":"response.text.delta","delta":"..."}` - incremental assistant text output.
  - `{"type":"response.audio.delta","delta":"<base64_pcm16>","sampleRate":24000,"channels":1,"format":"pcm16"}` - raw PCM16 base64 delta; frontend decodes and plays.
  - `{"type":"response.audio.done"}` - assistant finished speaking.
  - `{"type":"session.ready","message":"Connected to OpenAI"}` - server connected to OpenAI.
  - `{"type":"echo", ...}` - backend echo used when OpenAI is not connected (helpful in testing).

Audio encoding constraints: the frontend currently expects raw PCM16 little-endian samples at 24000 Hz, mono by default. If you change the backend audio encoding (e.g., Opus/MP3), the frontend must be updated to decode those formats accordingly.

---

## Deployment (Fly)

This project includes a `Procfile` and `fly.toml` for Fly.io. Key notes:

1. Fly provides `$PORT` automatically; the app is already configured to bind to `0.0.0.0` and read `PORT`.
2. Use Fly secrets to store keys:

```bash
flyctl auth login
flyctl secrets set OPENAI_API_KEY="sk-..."
# Optional: flyctl secrets set AMADEUS_CLIENT_ID=... AMADEUS_CLIENT_SECRET=...
```

3. Deploy:

```bash
flyctl deploy -a <app-name>
# Monitor logs
flyctl logs -a <app-name> --follow
```

4. Container builds and `pyaudio`:
- `pyaudio` requires `portaudio` system libraries. In container builds, `pyaudio` often causes build failures. Two options:
  - Remove `pyaudio` from `requirements.txt` for production and rely on browser audio only.
  - Keep `pyaudio` but add a feature flag / env var to skip opening audio streams in containers (recommended).

Suggested change to avoid container build issues: guard `pyaudio` initialization and the server-side audio code with an environment flag such as `ENABLE_LOCAL_AUDIO` and default it to `false` in production.

---

## Security / Secrets handling

1. Never commit `.env` files or private keys. Add `.env` to `.gitignore`.
2. If secrets were accidentally committed, rotate the exposed credentials immediately and purge history before pushing cleaned history to remote. Example using `git-filter-repo`:

```bash
# Make a backup clone first
git clone --mirror <repo> repo-mirror.git
cd repo-mirror.git
# Remove .env and any file or pattern containing secrets
git filter-repo --invert-paths --paths .env
# Push cleaned mirror to remote (force) after reviewing
git push --force --tags origin refs/heads/*
```

Alternative: use BFG Repo-Cleaner following BFG docs. Always rotate keys first before rewriting history, and inform collaborators they must re-clone.

---

## Troubleshooting

- Backend crashes or fails on import:
  - Verify `requirements.txt` dependencies installed in the active virtual environment.
  - Native build errors often point to `pyaudio`/portaudio issues.

- WebSocket failing to connect from browser:
  - Check backend logs and ensure `uvicorn` is running on the expected port.
  - Ensure `ws://` vs `wss://` matches page protocol. When using HTTPS, frontend uses `wss` automatically.

- Audio does not play in browser:
  - Verify messages in DevTools Console. Check that `response.audio.delta` messages include `delta` base64 and that the audio is PCM16 little-endian.
  - Confirm `AudioContext` is resumed on a user gesture; the frontend calls `audioContext.resume()` on Start.

- OpenAI quota/billing errors:
  - The backend attempts to detect quota errors and prints instructions. Check the `OPENAI_API_KEY` validity and your account billing page.

---

## Developer notes & recommended next steps

- Make `pyaudio` optional for production builds. For example, wrap `pyaudio` initialization in `if os.getenv('ENABLE_LOCAL_AUDIO') == '1':` guards.
- Add automated tests for WebSocket endpoints and a small dev-only route that returns a base64 PCM16 test tone so you can validate `playAudioDelta` without a live OpenAI session.
- Add CI linting (flake8/ruff) and unit tests to catch syntax and style issues.
- Consider moving the OpenAI realtime client into an async background task that integrates with FastAPI's lifecycle rather than a separate thread, to simplify shutdown logic.

---

## Files of interest (one-line descriptions)

- `index.html` — frontend UI and WebSocket client; decodes PCM16 and plays via Web Audio API.
- `setup.py` — FastAPI app, WebSocket `/ws` endpoint, OpenAI realtime bridge, and startup/shutdown hooks.
- `amadeus_api.py` — helper for hotel searches (optional tool integration).
- `Procfile` — runtime command for `uvicorn setup:app`.
- `dockerfile` — container image; includes `portaudio` system install and runs `uvicorn`.
- `requirements.txt` — Python dependencies.

---

## License

This repository does not include a license file. If you intend to publish this code, add an appropriate `LICENSE` file (for example, MIT or Apache-2.0).

---

If you would like, I can:

- Commit this README to the repo (`README.md`).
- Add a small developer test route to `index.html` that plays a base64 PCM16 tone for playback verification.
- Add guards around `pyaudio` so builds do not fail in container environments.

Tell me which of these follow-ups you want me to implement next and I will apply them.
