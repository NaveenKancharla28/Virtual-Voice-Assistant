# 🗣️ Virtual Voice Assistant

An AI-powered **voice assistant** built with **Python** that integrates **OpenAI’s GPT models** for intelligent conversation and **Amadeus API** for real-time hotel data.  
The assistant listens to user speech, converts it to text, generates an intelligent response, and replies back through voice.

---

## 🚀 Features

- 🎤 **Real-time Speech Recognition** — Converts voice to text using `pyaudio` and `speech_recognition`.
- 🧠 **AI-Generated Replies** — Uses OpenAI GPT models for natural, context-aware responses.
- 🏨 **Hotel Search via Amadeus API** — Fetches and speaks out hotel options based on user’s query.
- 🔊 **Text-to-Speech Output** — Responds through audio using `pyttsx3` (offline) or a TTS API.
- 🌐 **Web Interface** — Simple HTML frontend (`index.html`) to interact with the assistant.

---

## 🧩 Project Structure

Virtual-Voice-Assistant/
│
├── amadeus_api.py # Handles hotel search using Amadeus API
├── setup.py # Python dependencies and entry point
├── index.html # Frontend interface
├── pycache/ # Cached compiled files
└── .gitignore # Excludes env and venv files

yaml
Copy code

---

## ⚙️ Setup & Installation

###  Clone the Repository

git clone https://github.com/NaveenKancharla28/Virtual-Voice-Assistant.git
cd Virtual-Voice-Assistant


 Create Virtual Environment
bash
Copy code
python3 -m venv venv
source venv/bin/activate   # macOS / Linux
venv\Scripts\activate      # Windows

 Install Dependencies
bash
Copy code
pip install -r requirements.txt
If requirements.txt doesn’t exist, install manually:

bash
Copy code
pip install openai amadeus speechrecognition pyttsx3 pyaudio flask


Set Environment Variables
Create a .env file in the root directory:

ini
Copy code
OPENAI_API_KEY=your_openai_api_key
AMADEUS_CLIENT_ID=your_amadeus_client_id
AMADEUS_CLIENT_SECRET=your_amadeus_client_secret


Run the Application
bash
Copy code
python setup.py
Then open your browser at:

arduino
Copy code
http://localhost:5000
🧠 How It Works
User speaks into the microphone.

Audio is transcribed to text.

The text is sent to OpenAI’s API.

GPT generates a relevant, context-aware response.

If the query includes hotel search, amadeus_api.py fetches data.

The assistant speaks the response using text-to-speech.

🧪 Example Queries
“Find me hotels in Tokyo under $100.”

“What’s the weather like today?”

“Tell me a fun fact about AI.”

“Book a hotel near Osaka station.”

📦 Technologies Used
Component	Tech
Backend	Python (Flask)
AI Model	OpenAI GPT
Speech	SpeechRecognition, PyAudio
TTS	pyttsx3 / gTTS
Hotel API	Amadeus
Frontend	HTML5

🧭 Future Enhancements
🌍 Add voice-based flight search and booking.

💬 Add memory/context window for multi-turn conversations.

📱 Integrate mobile voice streaming with WebSockets.

🎨 Create a modern React-based UI.

🧑‍💻 Author
Naveen Kancharla
AI Engineer | Generative AI & Voice Applications
🔗 GitHub Profile
✉️ Contact: available via LinkedIn or project discussions.

🪪 License
This project is licensed under the MIT License — feel free to modify and build upon it.
