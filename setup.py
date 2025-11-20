import os
import json
import certifi
import ssl
import websocket
import threading
import pyaudio
import base64
import numpy as np
from dotenv import load_dotenv
from openai import OpenAI
from amadeus_api import search_hotels
import time
import asyncio
import websockets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY not found. Please set it in your .env file.")

# Audio settings
SAMPLE_RATE = 24000
CHUNK_SIZE = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 1
WS_URL = "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview"

# Global vars
p = pyaudio.PyAudio()
input_stream = None
output_stream = None
ws = None
reconnect_attempts = 0
MAX_RECONNECT_ATTEMPTS = 5
frontend_clients = []  # Track connected frontend WebSocket clients
is_recording = False  # Track recording state

db = None

# Simple in-memory conversation store (non-persistent) to replace Firestore usage
conversation_store = []

# Create FastAPI app
app = FastAPI()


@app.on_event("startup")
async def startup_event():
    """Start OpenAI realtime WS + mic streaming when the app boots (Fly + local)."""
    global output_stream

    try:
        print("Starting OpenAI realtime websocket background thread...")
        start_websocket()                  # connect to OpenAI
        threading.Thread(target=stream_mic_audio, daemon=True).start()
    except Exception as e:
        print(f"Failed to start websocket / mic thread: {e}")

    # optional: server-side speaker for local dev only
    try:
        output_stream = p.open(format=FORMAT, channels=CHANNELS, rate=SAMPLE_RATE, output=True)
        print("Output audio stream initialized.")
    except OSError as e:
        print(f"Failed to open output stream: {e}")
        output_stream = None


@app.on_event("shutdown")
async def shutdown_event():
    global ws, input_stream, output_stream

    try:
        if ws:
            ws.close()
    except Exception:
        pass

    for stream in (input_stream, output_stream):
        if stream:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass

    p.terminate()
    print("Shutdown complete.")

@app.on_event("startup")
async def startup_event():
    """Runs when FastAPI app starts (works in Fly with uvicorn setup:app)."""
    global output_stream

    # Start OpenAI realtime WebSocket in background
    try:
        start_websocket()
        print("Started OpenAI realtime websocket background thread.")
    except Exception as e:
        print(f"Failed to start websocket on startup: {e}")

    # Try to create audio output stream (for local dev). On Fly this may fail; that's OK.
    try:
        output_stream = p.open(format=FORMAT, channels=CHANNELS, rate=SAMPLE_RATE, output=True)
        print("Output audio stream initialized.")
    except OSError as e:
        print(f"Failed to open output stream (probably no audio device): {e}")
        output_stream = None


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup when server stops."""
    global ws, input_stream, output_stream

    try:
        if ws:
            ws.close()
    except Exception:
        pass

    if input_stream:
        try:
            input_stream.stop_stream()
            input_stream.close()
        except Exception:
            pass

    if output_stream:
        try:
            output_stream.stop_stream()
            output_stream.close()
        except Exception:
            pass

    p.terminate()
    print("Shutdown complete.")


# Serve static files (including index.html)
app.mount("/static", StaticFiles(directory="."), name="static")

# Serve index.html at root
@app.get("/")
async def get_index():
    return FileResponse("index.html")


@app.get("/health")
async def health_check():
    return {"status": "ok"}

def on_message(ws, message):
    event = json.loads(message)
    event_type = event.get('type')
    
    if event_type == 'response.audio.delta':
        audio_delta = np.frombuffer(base64.b64decode(event['delta']), dtype=np.int16)

        # Play on server only if we actually have an output device (locally)
        if output_stream is not None:
            try:
                output_stream.write(audio_delta.tobytes())
            except Exception as e:
                print(f"Output stream error: {e}")

        print("Forwarding Virtual Assistant delta to frontend...")
        # Forward audio to frontend with metadata
        for client in frontend_clients:
            try:
                asyncio.run_coroutine_threadsafe(
                    client.send_text(json.dumps({
                        "type": "response.audio.delta",
                        "delta": event['delta'],
                        "sampleRate": SAMPLE_RATE,
                        "channels": CHANNELS,
                        "format": "pcm16"
                    })),
                    asyncio.get_event_loop()
                )
            except Exception as e:
                print(f"Failed to forward audio to frontend: {e}")

    
    elif event_type == 'response.audio.done':
        print("Virtual Assistant response complete.")
        for client in frontend_clients:
            try:
                asyncio.run_coroutine_threadsafe(
                    client.send_text(json.dumps({"type": "response.audio.done"})),
                    asyncio.get_running_loop()
                )
            except Exception:
                pass
    
    elif event_type == 'session.created':
        print("✅ OpenAI Realtime session created successfully")
        print(f"Session ID: {event.get('session', {}).get('id', 'N/A')}")
        # Notify frontend that connection is ready
        for client in frontend_clients:
            try:
                asyncio.run_coroutine_threadsafe(
                    client.send_text(json.dumps({"type": "session.ready", "message": "Connected to OpenAI"})),
                    asyncio.get_running_loop()
                )
            except Exception:
                pass
     
    elif event_type == 'response.text.delta':
        delta_text = event.get('delta', '')
        print(f"Transcription delta: {delta_text}")
        # Store in-memory (non-persistent)
        try:
            conversation_store.append({"sender": "assistant", "message": delta_text, "timestamp": time.time()})
        except Exception:
            pass
        # Forward to frontend clients
        for client in frontend_clients:
            try:
                asyncio.run_coroutine_threadsafe(
                    client.send_text(json.dumps({"type": "response.text.delta", "delta": delta_text})),
                    asyncio.get_running_loop()
                )
            except Exception:
                pass
    
    elif event_type == 'response.function_call':
        call_id = event['call_id']
        tool_name = event['name']
        tool_params = json.loads(event['parameters'])
        print(f"Tool call: {tool_name} with params {tool_params}")
        
        if tool_name == 'search_hotels':
            result = search_hotels(tool_params)
            print(f"Tool result: {result}")
            tool_response = {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps(result)
                }
            }
            ws.send(json.dumps(tool_response))
            ws.send(json.dumps({"type": "response.create"}))
            # Forward result to frontend clients
            for client in frontend_clients:
                try:
                    asyncio.run_coroutine_threadsafe(
                        client.send_text(json.dumps(tool_response)),
                        asyncio.get_running_loop()
                    )
                except Exception:
                    pass
    
    
        elif event_type == 'error':
            error_info = event.get('error', {})
            error_code = error_info.get('code', '')
            error_message = error_info.get('message', str(error_info))
        
        # Detect quota/billing errors
        if error_code in ['insufficient_quota', 'rate_limit_exceeded', 'quota_exceeded']:
            print("\n" + "="*60)
            print("⚠️  OPENAI QUOTA/BILLING ERROR")
            print("="*60)
            print(f"Error Code: {error_code}")
            print(f"Message: {error_message}")
            print("\nACTION REQUIRED:")
            print("1. Check your OpenAI account billing: https://platform.openai.com/settings/organization/billing")
            print("2. Add credits or update payment method")
            print("3. Check usage limits: https://platform.openai.com/usage")
            print("="*60 + "\n")
        else:
            print(f"Error: {error_info}")
        
        # Forward error to frontend clients with enhanced message
        for client in frontend_clients:
            try:
                error_display = {
                    "type": "error",
                    "error": error_info,
                    "userMessage": "⚠️ Out of OpenAI credits. Please add funds to your account." if error_code in ['insufficient_quota', 'rate_limit_exceeded', 'quota_exceeded'] else error_message
                }
                asyncio.run_coroutine_threadsafe(
                    client.send_text(json.dumps(error_display)),
                    asyncio.get_running_loop()
                )
            except Exception:
                pass

def on_error(ws, error):
    print(f"WebSocket error: {error}")

def on_close(ws, close_status_code, close_reason):
    global reconnect_attempts
    print(f"WebSocket closed: {close_status_code}, {close_reason}")
    if reconnect_attempts < MAX_RECONNECT_ATTEMPTS:
        reconnect_attempts += 1
        print(f"Reconnecting attempt {reconnect_attempts}/{MAX_RECONNECT_ATTEMPTS}...")
        time.sleep(2 ** reconnect_attempts)  # Exponential backoff
        start_websocket()

def start_websocket():
    global ws
    # Run the OpenAI Realtime WebSocket in a background thread so the FastAPI server can run in the foreground.
    ws = websocket.WebSocketApp(
        WS_URL,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
        on_open=on_open,
        header=[f"Authorization: Bearer {OPENAI_API_KEY}", "OpenAI-Beta: realtime=v1"]
    )

    thread = threading.Thread(
        target=ws.run_forever,
        kwargs={"sslopt": {"cert_reqs": ssl.CERT_REQUIRED, "ca_certs": certifi.where()}},
        daemon=True,
    )
    thread.start()

def on_open(ws):
    global reconnect_attempts
    reconnect_attempts = 0
    print("WebSocket connected.")
    # Minimal, supported session.update payload for the realtime API
    session_update = {
        "type": "session.update",
        "session": {
            "instructions": "You are a helpful voice assistant. For hotel searches, use the search_hotels tool with at least cityCode (IATA code like PAR for Paris) and checkInDate (YYYY-MM-DD). Respond quickly with top results.",
            "voice": "alloy",
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm16",
            "turn_detection": {"type": "server_vad"},
            "temperature": 0.7,
            "tools": [
                {
                    "type": "function",
                    "name": "search_hotels",
                    "description": "Search Amadeus for hotels, sorted by price ascending. Requires at least cityCode (IATA) and checkInDate (YYYY-MM-DD).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "cityCode": {"type": "string", "description": "IATA city code, e.g., PAR"},
                            "checkInDate": {"type": "string", "description": "Check-in date YYYY-MM-DD"},
                            "checkOutDate": {"type": "string", "description": "Check-out date YYYY-MM-DD"},
                            "adults": {"type": "integer", "description": "Number of adults"},
                            "roomQuantity": {"type": "integer", "description": "Number of rooms"}
                        },
                        "required": ["cityCode", "checkInDate"]
                    }
                }
            ]
        }
    }
    ws.send(json.dumps(session_update))
    threading.Thread(target=stream_mic_audio, daemon=True).start()

def stream_mic_audio():
    global input_stream, ws, is_recording
    try:
        input_stream = p.open(format=FORMAT, channels=CHANNELS, rate=SAMPLE_RATE, input=True, frames_per_buffer=CHUNK_SIZE)
    except OSError as e:
        print(f"Failed to open input stream: {e}. Check microphone permissions or device availability.")
        return
    while True:
        try:
            if is_recording and ws and ws.sock and ws.sock.connected:
                data = input_stream.read(CHUNK_SIZE, exception_on_overflow=False)
                audio_event = {
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(data).decode('utf-8')
                }
                ws.send(json.dumps(audio_event))
            else:
                time.sleep(0.1)  # Avoid busy waiting when not recording
        except websocket.WebSocketConnectionClosedException:
            print("WebSocket closed during audio streaming, attempting reconnect...")
            break
        except Exception as e:
            print(f"Audio streaming error: {e}")
            break
    if input_stream:
        input_stream.close()

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    frontend_clients.append(websocket)
    global is_recording
    try:
        while True:
            # FastAPI's WebSocket doesn't support async iteration; use receive_text/receive_json
            message = await websocket.receive_text()
            print(f"Received from frontend: {message}")
            try:
                data = json.loads(message)
            except Exception:
                data = {}

            if ws and getattr(ws, 'sock', None) and getattr(ws.sock, 'connected', False):
                # Handle control messages locally without forwarding to OpenAI
                if data.get("type") == "input_audio_buffer.append":
                    audio_b64 = data.get("audio", "")
                    if audio_b64 == base64.b64encode(b"start_recording").decode('utf-8'):
                        is_recording = True
                        print("Started recording")
                        continue  # Don't forward this control message to OpenAI
                    elif audio_b64 == base64.b64encode(b"stop_recording").decode('utf-8'):
                        is_recording = False
                        print("Stopped recording")
                        continue  # Don't forward this control message to OpenAI
                
                # Forward valid messages to OpenAI websocket
                try:
                    ws.send(message)
                except Exception as e:
                    print(f"Failed to forward message to OpenAI WS: {e}")
            else:
                # OpenAI WS not connected — echo back the received message for testing/deployment
                try:
                    echo_payload = {"type": "echo", "received": data if data else {"raw": message}}
                    await websocket.send_text(json.dumps(echo_payload))
                except Exception:
                    # Fallback simple echo
                    await websocket.send_text(message)
    except Exception as e:
        # Distinguish disconnects for cleaner logs
        if isinstance(e, WebSocketDisconnect):
            print("Frontend disconnected")
        else:
            print(f"Frontend handler error: {e}")
    finally:
        try:
            frontend_clients.remove(websocket)
        except ValueError:
            pass
        print(f"Frontend clients remaining: {len(frontend_clients)}")

# Update the start_frontend_ws function to use FastAPI
async def start_frontend_ws():
    try:
        import uvicorn
        port = int(os.getenv("PORT", "8080"))
        config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()
    except Exception as e:
        print(f"Failed to start frontend WebSocket server: {e}")

