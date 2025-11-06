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
import firebase_admin
from firebase_admin import credentials, firestore
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

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

# Initialize Firebase with credentials from .env
cred = credentials.Certificate({
    "type": os.getenv("FIREBASE_TYPE"),
    "project_id": os.getenv("FIREBASE_PROJECT_ID"),
    "private_key_id": os.getenv("FIREBASE_PRIVATE_KEY_ID"),
    "private_key": os.getenv("FIREBASE_PRIVATE_KEY").replace("\\n", "\n"),
    "client_email": os.getenv("FIREBASE_CLIENT_EMAIL"),
    "client_id": os.getenv("FIREBASE_CLIENT_ID"),
    "auth_uri": os.getenv("FIREBASE_AUTH_URI"),
    "token_uri": os.getenv("FIREBASE_TOKEN_URI"),
    "auth_provider_x509_cert_url": os.getenv("FIREBASE_AUTH_PROVIDER_X509_CERT_URL"),
    "client_x509_cert_url": os.getenv("FIREBASE_CLIENT_X509_CERT_URL")
})
firebase_admin.initialize_app(cred)
db = firestore.client()

# Create FastAPI app
app = FastAPI()

# Serve static files (including index.html)
app.mount("/static", StaticFiles(directory="."), name="static")

# Serve index.html at root
@app.get("/")
async def get_index():
    return FileResponse("index.html")

def on_message(ws, message):
    event = json.loads(message)
    event_type = event.get('type')
    
    if event_type == 'response.audio.delta':
        audio_delta = np.frombuffer(base64.b64decode(event['delta']), dtype=np.int16)
        output_stream.write(audio_delta.tobytes())
        print("Playing Virtual Assistant delta on server...")
        # Forward audio to frontend with metadata
        for client in frontend_clients:
            if client.open:
                asyncio.run_coroutine_threadsafe(
                    client.send(json.dumps({
                        "type": "response.audio.delta",
                        "delta": event['delta'],
                        "sampleRate": SAMPLE_RATE,
                        "channels": CHANNELS,
                        "format": "pcm16"
                    })),
                    asyncio.get_running_loop()
                )
    
    elif event_type == 'response.audio.done':
        print("Virtual Assistant response complete.")
        for client in frontend_clients:
            if client.open:
                asyncio.run_coroutine_threadsafe(
                    client.send(json.dumps({"type": "response.audio.done"})),
                    asyncio.get_running_loop()
                )
    
    elif event_type == 'input_audio_buffer.speech_started':
        print("User started speaking - interrupt if needed.")
     
    elif event_type == 'response.text.delta':
        print(f"Transcription delta: {event.get('delta', '')}")
        user_id = "naveenchaitanya"
        doc_ref = db.collection('conversations').document("naveenchaitanya").collection('messages').document()
        doc_ref.set({
            "sender": "assistant",
            "message": event.get('delta', ''),
            "timestamp": firestore.SERVER_TIMESTAMP
        })
        # Forward to frontend clients
        for client in frontend_clients:
            if client.open:
                asyncio.run_coroutine_threadsafe(
                    client.send(json.dumps({"type": "response.text.delta", "delta": event.get('delta', '')})),
                    asyncio.get_running_loop()
                )
    
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
                if client.open:
                    asyncio.run_coroutine_threadsafe(
                        client.send(json.dumps(tool_response)),
                        asyncio.get_running_loop()
                    )
    
    elif event_type == 'error':
        print(f"Error: {event['error']}")
        # Forward error to frontend clients
        for client in frontend_clients:
            if client.open:
                asyncio.run_coroutine_threadsafe(
                    client.send(json.dumps({"type": "error", "error": event['error']})),
                    asyncio.get_running_loop()
                )
            elif event_type == 'error':
                print(f"Error: {event['error']}")
            for client in frontend_clients:
             if client.open:
                asyncio.run_coroutine_threadsafe(
                    client.send(json.dumps({"type": "error", "error": event['error']})),
                    asyncio.get_event_loop()
                )

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
                if data.get("type") == "input_audio_buffer.append":
                    if data.get("audio") == base64.b64encode(b"start_recording").decode('utf-8'):
                        is_recording = True
                        print("Started recording")
                    elif data.get("audio") == base64.b64encode(b"stop_recording").decode('utf-8'):
                        is_recording = False
                        print("Stopped recording")
                # forward raw message string to OpenAI websocket
                try:
                    ws.send(message)
                except Exception as e:
                    print(f"Failed to forward message to OpenAI WS: {e}")
            else:
                await websocket.send(json.dumps({"type": "error", "error": "OpenAI WebSocket not connected"}))
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
        config = uvicorn.Config(app, host="localhost", port=8080, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()
    except Exception as e:
        print(f"Failed to start frontend WebSocket server: {e}")

# Main entrypoint - start background tasks and run FastAPI/uvicorn in foreground
if __name__ == "__main__":
    try:
        # Start OpenAI realtime WebSocket in background
        start_websocket()

        # Setup output stream (optional)
        try:
            output_stream = p.open(format=FORMAT, channels=CHANNELS, rate=SAMPLE_RATE, output=True)
        except OSError as e:
            print(f"Failed to open output stream: {e}. Check audio output device availability.")
            output_stream = None

        # Run FastAPI / Uvicorn (this call blocks until server stops)
        import uvicorn
        uvicorn.run(app, host="localhost", port=8080)

    except KeyboardInterrupt:
        print("Interrupted by user, shutting down...")
    finally:
        # cleanup
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
        print("Exited.")