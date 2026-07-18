# Portal-MacOS

Pre-Release Notice: Portal is currently in active development and should be considered a pre-release version. Features may change, bugs may be present, and certain functionality may be incomplete as development continues. Feedback, bug reports, and suggestions are welcome.
Portal is a lightweight desktop application that gives users instant access to multiple AI providers from a single floating widget. Instead of managing countless browser tabs, Portal keeps your favorite LLMs one click away while remaining accessible from anywhere on your desktop.

✨ Why Portal?

Modern AI users often switch between multiple models for different tasks. Keeping separate browser windows open for every provider creates clutter and interrupts workflow.

Portal solves this by providing:

One interface for multiple AI providers
Fast desktop access without opening a browser
Persistent sessions across launches
A clean and distraction-free workspace
🚀 Features

Floating Widget

Always accessible desktop widget
Expand and collapse with smooth animations
Minimal screen usage when inactive
Quick access from anywhere on your desktop
Multi-Provider Support

ChatGPT integration
Claude integration
Gemini integration
Easily extensible for additional providers
Persistent Sessions

Saves previously opened chats
Restores the most recent conversation on startup
Maintains provider-specific session data
Modern Desktop Experience

Smooth opening and closing animations
Dynamic provider tabs
Responsive UI interactions
Native desktop feel
🛠️ Built With

Python
PySide6
Qt WebEngine
JavaScript
HTML/CSS
⚙️ How It Works

Portal embeds AI provider web applications using Qt WebEngine
User authentication is handled directly through provider websites
Settings and session information are stored locally
The application dynamically manages multiple AI providers through a unified interface
📋 Requirements

Python 3.10+
PySide6
Qt WebEngine
Internet connection
Valid accounts for supported AI providers
📦 Installation

Clone the Repository

git clone https://github.com/yourusername/portal.git
cd portal
Install Dependencies

pip install -r requirements.txt
Launch Portal

python main.py
🔒 Privacy

No external Portal servers are used
Authentication occurs directly with provider websites
User settings remain local to the device
Portal does not store or process chat data externally
📄 License

Copyright (c) 2026 Adam Lashnuk, Faris Felamban, Ahmed Abuharba
