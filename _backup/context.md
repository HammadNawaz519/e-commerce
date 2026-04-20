# CHIT CHAT — Full Project Context

# This document is intended as an AI training/context reference.

# It contains exhaustive descriptions of every component, decision, and pattern in the codebase.

---

## 1. PROJECT IDENTITY

**Name:** Chit Chat  
**Type:** Real-time web chat & calling application  
**Version:** 1.0  
**Stack:** Python/Flask backend, Vanilla JS frontend, MySQL database, Socket.IO real-time layer, WebRTC calling  
**Design language:** Premium glassmorphic (ivory/sand/charcoal palette, Plus Jakarta Sans, heavy border-radius)

---

## 2. APPLICATION PURPOSE

Chit Chat is a WhatsApp-inspired web messaging app. Users register with a username, phone number, email, and password. Email OTP verification gates registration. Once logged in, users can:

1. Search for other registered users
2. Chat in real-time (text, voice messages, images, videos)
3. See online/offline status of contacts
4. View read receipts on sent messages
5. Make peer-to-peer audio calls (WebRTC, UDP)
6. Make peer-to-peer video calls (WebRTC, UDP)
7. Manage their profile (avatar, username, etc.)

The whole application runs in-browser with no native app required.

---

## 3. REPOSITORY FILE TREE

```
CHIT-CHAT/
├── app.py                    # Flask application — ALL backend logic
├── requirements.txt          # Python package list
├── .env                      # Runtime secrets (not committed)
├── README.md                 # Developer documentation
├── context.md                # This file — AI training context
├── static/
│   ├── style.css             # Legacy stub stylesheet (largely unused)
│   ├── main.js               # Legacy JS stub (largely unused)
│   ├── Images/
│   │   └── chit_chat.png     # App logo used as favicon + branding mark
│   └── uploads/
│       ├── images/           # User-uploaded image message files
│       ├── voice/            # User-uploaded voice message .webm files
│       ├── videos/           # User-uploaded video message .mp4 files
│       └── profile_pics/     # User avatar uploads (jpg/png/etc.)
└── templates/
    ├── auth.html             # Auth page (login + register, single-page with JS transitions)
    ├── verify.html           # OTP verification screen
    ├── chat.html             # Main chat interface (messaging + calling triggers)
    ├── call.html             # Full-screen WebRTC call UI (new window)
    └── profile.html          # Profile management page
```

---

## 4. BACKEND — app.py

### 4.1 Initialization

```python
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    manage_session=False,
    max_http_buffer_size=10_000_000   # 10 MB max message (for base64 media)
)

online_users = {}  # { user_id (int): set of socket_session_id }
#
# each connected browser window/tab opens a separate Socket.IO socket.  the
# original implementation kept only one sid per user, so opening a second
# socket (e.g. the call popup) would overwrite the first.  when the popup
# closed the remaining chat socket was forgotten and the user was marked
# offline.  we now maintain a set and only broadcast offline when the set
# becomes empty.
```

- `manage_session=False` is critical — it tells Flask-SocketIO not to interfere with Flask's session, allowing `session['user_id']` to be read inside socket event handlers.
- `max_http_buffer_size` is raised to 10 MB to allow base64-encoded media to be sent through Socket.IO events.

### 4.2 Database Helper

```python
def get_db():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME"),
        autocommit=True
    )
```

- A new connection is opened per request/event call and closed immediately after use.
- `autocommit=True` means INSERT/UPDATE queries apply without an explicit `.commit()` call.
- The database is MySQL, accessed via `mysql-connector-python`.

### 4.3 Authentication System

**Registration flow:**

1. User submits username, phone, email, password via POST `/register`
2. Password is SHA-256 hashed
3. Duplicate check on email + phone number
4. 6-digit OTP generated and emailed via Flask-Mail (Gmail SMTP)
5. OTP + registration data stored in Flask session
6. User redirected to `/verify`

**Verification flow:**

1. User enters OTP at `/verify`
2. If OTP matches: user record is INSERTed into `users` table with `verified=1`
3. Redirect to `/login`

**Login flow:**

1. User submits phone number + password via POST `/login`
2. Password is SHA-256 hashed, checked against DB
3. On match: `session['user_id'] = user['id']`
4. Redirect to `/chat`

**All protected routes** check `session.get('user_id')` and redirect to `/login` if absent.

### 4.4 Database Schema

#### users table

| Column       | Type         | Notes                                   |
| ------------ | ------------ | --------------------------------------- |
| id           | INT PK AI    | Primary key                             |
| username     | VARCHAR(100) | Display name                            |
| phone_number | VARCHAR(20)  | UNIQUE, used for login                  |
| email        | VARCHAR(150) | UNIQUE, used for OTP                    |
| password     | VARCHAR(256) | SHA-256 hex digest                      |
| verified     | TINYINT(1)   | 1 = email verified                      |
| profile_pic  | VARCHAR(300) | Relative path e.g. `profile_pics/x.png` |
| created_at   | DATETIME     | DEFAULT CURRENT_TIMESTAMP               |

#### messages table

| Column      | Type                                         | Notes                         |
| ----------- | -------------------------------------------- | ----------------------------- |
| id          | INT PK AI                                    | Primary key                   |
| sender_id   | INT FK → users.id                            |                               |
| receiver_id | INT FK → users.id                            |                               |
| message     | LONGTEXT                                     | Text content or file URL path |
| type        | ENUM('text','voice','image','video','call')  | Message content type          |
| is_seen     | TINYINT(1)                                   | 0 = unread, 1 = seen          |
| timestamp   | DATETIME                                     | DEFAULT CURRENT_TIMESTAMP     |

### 4.5 HTTP Routes

| Route                  | Auth | Method   | Purpose                                             |
| ---------------------- | ---- | -------- | --------------------------------------------------- |
| `/`                    | No   | GET      | Redirect to `/login`                                |
| `/login`               | No   | GET/POST | Login form + logic                                  |
| `/register`            | No   | POST     | Register new user, send OTP, redirect to verify     |
| `/verify`              | No   | GET/POST | OTP verification page                               |
| `/chat`                | Yes  | GET      | Render `chat.html` with `my_id` injected            |
| `/call.html`           | Yes  | GET      | Render `call.html` (WebRTC call UI)                 |
| `/call`                | Yes  | GET      | Alias for `/call.html`                              |
| `/profile`             | Yes  | GET      | Render `profile.html`                               |
| `/update_profile`      | Yes  | POST     | Handle avatar file upload, save to uploads/         |
| `/remove_profile_pic`  | Yes  | GET      | Delete avatar file and clear DB column              |
| `/logout`              | Yes  | GET      | `session.clear()`, redirect to login                |
| `/search_users?q=`     | Yes  | GET      | Username prefix search, returns JSON array          |
| `/recent_chats`        | Yes  | GET      | Recent conversation list with last message preview  |
| `/messages/<other_id>` | Yes  | GET      | Full message history between current user and other |

### 4.6 Socket.IO Event Handlers

#### `connect`

- Triggered when a client opens a Socket.IO connection
- Reads `session['user_id']`, joins that user to their private room (`str(uid)`)
- Adds to `online_users` dict
- Broadcasts `user_status { user_id, online: True }` to everyone
- Emits `online_users_list` of current online user IDs back to the connecting client

#### `disconnect`

- Removes user from `online_users`
- Broadcasts `user_status { user_id, online: False }` to everyone

#### `user_online`

- Manually re-register a user as online (used on page focus / re-renders)
- Updates `online_users`, broadcasts status + full list

#### `join`

- `{ room }` — joins a chat room (format: `chat_{min_id}_{max_id}`)

#### `send_message`

- `{ sender, receiver, message, type }` where type is `'text'|'voice'|'image'|'video'`
- For binary types (voice/image/video): message is a base64 data URL
  - Data is decoded and saved to the appropriate uploads subfolder
  - `message` column is replaced with the static URL path
- Inserts into `messages` table
- Emits `receive_message` to the shared chat room
- Emits `update_recents` to both sender's and receiver's private rooms

#### `mark_as_read`

- `{ sender_id, receiver_id }` — marks all unread messages from sender_id to receiver_id as `is_seen=1`
- Emits `messages_read { reader_id }` to sender's private room

#### `incoming_call_notification`

- `{ caller, callee, room, type }` — relays a call notification to the callee's private Socket.IO room
- Callee receives this and shows an incoming call overlay

#### `join_call_room`

- `{ room }` — joins a WebRTC signaling room
- Emits `peer_ready { userId }` to all others in the room (triggers caller to create an SDP offer)

#### WebRTC Signaling Relay (all just relay to the room):

- `call_offer` → relays `{ room, sdp }` to others in room (exclude self)
- `call_answer` → relays `{ room, sdp }` to others in room (exclude self)
- `ice_candidate` → relays `{ room, candidate }` to others in room (exclude self)
- `call_ended` → relays `{ room }` to all in room, then the handler leaves the room

---

## 5. FRONTEND — chat.html

The main chat interface. Self-contained: all CSS and JS are inline in this one file (2000+ lines). No external JS frameworks.

### 5.1 Layout

```
┌──────────────────────────────────────────────────────────┐
│  HEADER (.app-top)                                        │
│  [Logo] [Chit Chat]    [My ID: X]  [Profile avatar]      │
├──────────────┬───────────────────────────────────────────┤
│  SIDEBAR     │  CHAT AREA                                │
│  (.sidebar)  │  (.chat-area)                             │
│              │  ┌────────────────────────────────────┐  │
│  [Search]    │  │  CHAT HEADER (.chat-header)         │  │
│              │  │  [← Back] [Avatar] [Name] [🔊][📹]  │  │
│  Recent      │  ├────────────────────────────────────┤  │
│  conversations│  │  MESSAGES (.messages)               │  │
│  list        │  │  [msg bubble] [msg bubble] ...      │  │
│  (.list)     │  ├────────────────────────────────────┤  │
│              │  │  FOOTER (.footer)                   │  │
│              │  │  [📎][😊][___input___] [🎤/Send]    │  │
└──────────────┴───────────────────────────────────────────┘
```

On mobile (≤980px): sidebar and chat area are full-screen panels that slide in/out. Back button navigates from chat to contacts list.

### 5.2 Key JavaScript Variables

```javascript
const myId = parseInt(...)           // Current user's ID (from data attribute)
const socket = io()                  // Socket.IO client instance
let currentRoom = null               // Active chat room string
let currentReceiver = null           // Active chat partner's user ID
let mediaRecorder = null             // MediaRecorder for voice messages
let audioChunks = []                 // Voice recording buffer
let onlineUsers = new Set()          // Set of currently online user IDs
let isSending = false                // Send lock (prevents double-send)
let sendCooldown = false             // Send cooldown flag
```

### 5.3 Core Functions

| Function                | Description                                          |
| ----------------------- | ---------------------------------------------------- |
| `loadRecentChats()`     | Fetch `/recent_chats`, render sidebar list           |
| `openChat(id, ...)`     | Select a user, join their room, load message history |
| `sendMessage()`         | Emit `send_message` with current text input          |
| `handleVoice()`         | Start/stop MediaRecorder, emit base64 voice blob     |
| `appendMessage(data)`   | Render a message bubble in the messages div          |
| `updateOnlineUI()`      | Update all presence dots + chat header status badge  |
| `renderEmojis(cat)`     | Populate emoji picker grid for a given category      |
| `initiateCall(type)`    | Emit call notification, open call.html in new window |
| `showIncomingCall(d)`   | Display incoming call overlay, play ring tone        |
| `acceptIncomingCall()`  | Open call.html as callee, dismiss overlay            |
| `declineIncomingCall()` | Emit call_ended, dismiss overlay                     |

### 5.4 Message Types

| type    | `message` content                       | Rendered as        |
| ------- | --------------------------------------- | ------------------ |
| `text`  | UTF-8 string                            | `<div>` with text  |
| `voice` | Static URL `/static/uploads/voice/...`  | `<audio controls>` |
| `image` | Static URL `/static/uploads/images/...` | `<img>` clickable  |
| `video` | Static URL `/static/uploads/videos/...` | `<video controls>` |

### 5.5 Calling Trigger

```javascript
function initiateCall(type) {               // type = 'audio' | 'video'
    const room = 'chat_' + Math.min(...) + '_' + Math.max(...);
    socket.emit('incoming_call_notification', { caller: myId, callee: currentReceiver, room, type });
    const url = `/call.html?myId=...&peerId=...&isCaller=true&type=...`;
    window.open(url, '_blank', 'noopener,noreferrer,width=900,height=700');
}
```

The call room name is identical to the chat room name (`chat_{min}_{max}`) so the call and chat share topology but Socket.IO rooms are separate per `join_call_room`.

### 5.6 Incoming Call Overlay

A full-viewport overlay (`#incomingCallOverlay`) that appears when `incoming_call_notification` is received. Shows:

- Caller avatar (fetched from `/recent_chats`)
- Caller name
- Call type badge (Audio/Video)
- Pulsing ring animation
- Elapsed ringing timer
- Accept (green) / Decline (red) buttons
- Web Audio API ringtone (no audio file needed)
- Auto-dismisses after 30 seconds

---

## 6. FRONTEND — call.html

A full-screen page opened as a popup window. Handles the entire WebRTC call lifecycle.

### 6.1 URL Parameters

| Param      | Example         | Description                            |
| ---------- | --------------- | -------------------------------------- |
| `myId`     | `42`            | Current user's database ID             |
| `peerId`   | `17`            | Remote peer's database ID              |
| `isCaller` | `true`/`false`  | Whether this window initiated the call |
| `type`     | `audio`/`video` | Call modality                          |

The call room = `call_{min(myId,peerId)}_{max(myId,peerId)}`.

### 6.2 Layout (Full-screen, dark)

```
┌─────────────────────────────────────────────────────────────┐
│  BACKGROUND (gradient + animated glow blobs)                │
│                                                             │
│  TOP BAR                [Peer Name]            [0:00]       │
│                                                             │
│                   ┌──────────────┐                          │
│                   │  Avatar Ring  │  ← pulse rings          │
│                   │   [avatar]   │                          │
│                   └──────────────┘                          │
│                   Peer Name                                 │
│                   Calling… / 0:34                           │
│                   [Audio Call badge]                        │
│                                                             │
│                            ┌──────────┐                     │
│                            │ Local PIP│ ← draggable         │
│                            │ (video)  │                      │
│                            └──────────┘                     │
│                                                             │
│  REMOTE VIDEO (full-screen, video calls)                    │
│                                                             │
│  BOTTOM CONTROLS (glass bar)                                │
│  [Mute] [Camera] [  End  ] [Speaker] [Flip]                │
└─────────────────────────────────────────────────────────────┘
```

### 6.3 WebRTC Configuration

```javascript
const RTC_CONFIG = {
  iceServers: [
    { urls: "stun:stun.l.google.com:19302" },
    { urls: "stun:stun1.l.google.com:19302" },
    { urls: "stun:stun2.l.google.com:19302" },
    { urls: "stun:stun3.l.google.com:19302" },
    { urls: "stun:stun4.l.google.com:19302" },
  ],
  iceCandidatePoolSize: 10,
  iceTransportPolicy: "all", // Prefer UDP (host/srflx), allow relay fallback
};
```

All five Google STUN servers used for resilience. Media transport is UDP (SRTP/DTLS). Signaling only travels over Socket.IO (TCP/WebSocket).

### 6.4 Signaling State Machine

```
CALLER side:
  init()
    ↓ getMedia() → socket.emit('join_call_room')
    ↓ socket.on('peer_ready')
    ↓ createPC() → pc.createOffer()
    ↓ pc.setLocalDescription(offer) → socket.emit('call_offer')
    ↓ socket.on('call_answer')
    ↓ pc.setRemoteDescription(answer)
    ↓ ICE exchange (via 'ice_candidate' relay)
    ↓ pc.ontrack → setState('active')

CALLEE side:
  init()
    ↓ getMedia() → socket.emit('join_call_room') [→ triggers peer_ready to caller]
    ↓ socket.on('call_offer')
    ↓ createPC() → pc.setRemoteDescription(offer)
    ↓ pc.createAnswer() → pc.setLocalDescription(answer)
    ↓ socket.emit('call_answer')
    ↓ ICE exchange (via 'ice_candidate' relay)
    ↓ pc.ontrack → setState('active')
```

### 6.5 ICE Candidate Buffering

A critical implementation detail: ICE candidates can arrive before `setRemoteDescription()` completes. The implementation uses a `pendingCandidates` queue:

```javascript
let pendingCandidates = [];
let remoteDescSet = false;

socket.on("ice_candidate", async ({ candidate }) => {
  if (pc && remoteDescSet) {
    await pc.addIceCandidate(new RTCIceCandidate(candidate));
  } else {
    pendingCandidates.push(candidate); // buffer it
  }
});

// After setRemoteDescription() succeeds:
remoteDescSet = true;
for (const c of pendingCandidates) {
  await pc.addIceCandidate(new RTCIceCandidate(c));
}
pendingCandidates = [];
```

### 6.6 Media Constraints

```javascript
{
    audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl:  true,
        sampleRate:       48000,
        channelCount:     1,
    },
    video: CALL_TYPE === 'video' ? {
        facingMode: 'user',
        width:  { ideal: 1280 },
        height: { ideal: 720  },
        frameRate: { ideal: 30 },
    } : false
}
```

### 6.7 UI States

| State        | Visual                                          |
| ------------ | ----------------------------------------------- |
| `ringing`    | Pulsing rings, "Calling…", outgoing ring tone   |
| `connecting` | Loading spinner overlay, "Connecting…"          |
| `active`     | Timer running, rings stop, remote video visible |
| `ended`      | Overlay with duration, window closes in 3.2s    |

### 6.8 Controls

| Control     | Default | Off state                        |
| ----------- | ------- | -------------------------------- |
| Mute        | enabled | Mic muted, red tint icon         |
| Camera      | enabled | Video track disabled, red tint   |
| End Call    | —       | Hangs up, emits `call_ended`     |
| Speaker     | enabled | Remote audio muted               |
| Flip Camera | front   | Toggles front/rear (mobile only) |

### 6.9 Draggable Local PIP

The local video preview (`#localWrap`) is draggable via pointer/touch events. It can be repositioned anywhere on screen during a video call.

### 6.10 Auto-hide Controls (Video)

In video mode, controls auto-hide after 5 seconds to show the full remote video. Tapping anywhere on the "tap zone" overlay toggles control visibility.

### 6.11 Ringtone

Generated entirely via Web Audio API. No audio files needed. A simple two-tone oscillator pattern (440→540 Hz sine wave) plays every 1.8 seconds on the caller side until `peer_ready` fires.

---

## 7. FRONTEND — auth.html

Single-page (no navigation) with JavaScript-driven transitions between:

- **Login panel** — phone number + password
- **Register panel** — username + phone + email + password

Uses CSS animations (`fade-out` / `fade-in`) to crossfade text on the left decorative panel and slide the form panel right. No page reload between login and register views.

---

## 8. FRONTEND — profile.html

Shows:

- Current avatar (or initial letter placeholder)
- Username, email, phone
- File upload form for new avatar
- Remove avatar button
- Back to chat link

Avatar is constrained to common image types. Saved as `pfp_{userId}_{uuid}.{ext}` in `static/uploads/profile_pics/`.

---

## 9. FRONTEND — verify.html

Simple OTP entry form:

- Single 6-digit code input
- POST to `/verify`
- Shows error if OTP wrong
- Redirect to login on success

---

## 10. DESIGN SYSTEM TOKENS

All pages share the same CSS variable set:

```css
:root {
  --charcoal: #1a1c23; /* Primary dark — text, dark bubbles */
  --muted: #8e9bae; /* Secondary text, placeholder */
  --sand: #e1d9bc; /* Accent — received bubbles, highlights */
  --ivory: #f0f0db; /* Page background */
  --cream: #faf9f6; /* Gradient secondary bg */
  --white: #ffffff; /* Card faces */
  --glass: rgba(255, 255, 255, 0.85); /* Frosted glass panels */
  --glass-border: rgba(245, 244, 238, 0.5); /* Glass panel borders */
  --blur: 24px; /* Backdrop blur amount */
  --card-radius: 40px; /* Outer card corners */
  --item-radius: 24px; /* List item corners */
  --gap: 20px; /* Spacing unit */
  --shadow-sm: 0 4px 20px rgba(26, 28, 35, 0.06);
  --shadow-md: 0 8px 30px rgba(26, 28, 35, 0.08);
  --shadow-lg: 0 20px 60px rgba(26, 28, 35, 0.12);
  --shadow-premium:
    0 12px 40px rgba(26, 28, 35, 0.08), 0 4px 12px rgba(26, 28, 35, 0.04);
}
```

**Call page uses a separate dark theme:**

```css
:root {
  --call-bg: #0d0d0f;
  --call-dark: #1a1a1f;
  --glass: rgba(255, 255, 255, 0.09);
  --glass-border: rgba(255, 255, 255, 0.14);
  --danger: #ff3b30;
  --success: #34c759;
}
```

---

## 11. COMMUNICATION PATTERNS

### Presence System

- On socket `connect`: the user ID is put into `online_users` dict and a broadcast goes out
- All clients maintain a `Set<userId>` of online users
- Each list item and the chat header show a green dot (`#4ADE80`) if the peer is online
- On `disconnect` the user is removed and offline broadcast goes out

### Message Delivery

1. Sender emits `send_message` with sender/receiver IDs, message content, and type
2. Server saves to DB, emits `receive_message` to the shared room
3. Receiver's client appends the bubble; sender sees it too (from the socket event)
4. When receiver opens the chat, `mark_as_read` is emitted → DB updated → `messages_read` emitted back to sender → sender's bubbles show blue ticks

### Recent Chats

- On page load + after every new message, the sidebar calls `/recent_chats`
- The endpoint does a self-join to show unique conversation partners with the latest message timestamp
- If the last message is a file URL, it's shown as "Voice message", "Sent a photo", etc.

### User Search

- The search input debounces 300ms, calls `/search_users?q=<prefix>`
- Results replace the recent chats list; clearing the search restores recents

---

## 12. SECURITY MODEL

| Concern            | Implementation                                                                |
| ------------------ | ----------------------------------------------------------------------------- |
| Password storage   | SHA-256 hex digest (single hash, no salt — upgrade to bcrypt/argon2 for prod) |
| Session management | Flask signed cookie sessions                                                  |
| Route protection   | `session.get('user_id')` guard on all authenticated routes                    |
| OTP validity       | Single-use, stored in session, cleared on successful verify                   |
| File uploads       | Extension extracted and used to name file; base upload dirs pre-created       |
| CORS               | `cors_allowed_origins="*"` — should be restricted in production               |
| HTTPS              | Required in production for WebRTC (`getUserMedia` needs secure context)       |

---

## 13. CALLING SYSTEM — UDP TRANSPORT DETAILS

### Why WebRTC = UDP

WebRTC media is fundamentally UDP-based:

```
Browser A                                      Browser B
   │                                               │
   │  Socket.IO (TCP) — signaling only             │
   │──── SDP Offer ──────────────────────────────►│
   │◄─── SDP Answer ─────────────────────────────│
   │──── ICE candidates ─────────────────────────►│
   │◄─── ICE candidates ─────────────────────────│
   │                                               │
   │  WebRTC Media (UDP/SRTP) — direct P2P         │
   │══════════════════════════════════════════════►│
   │◄═════════════════════════════════════════════│
```

**ICE (Interactive Connectivity Establishment):** Gathers candidates (local IP, STUN-reflected IP, TURN relay). Candidates represent UDP endpoints. The browser tests them in priority order and picks the best UDP path.

**DTLS (Datagram TLS):** Key exchange protocol running over UDP. Establishes shared keys.

**SRTP (Secure Real-time Transport Protocol):** Media (audio/video frames) encrypted with DTLS keys, sent over UDP. No retransmission — lost packets are simply dropped (acceptable for real-time media).

### Why Not TCP?

TCP's retransmission and head-of-line blocking introduce unacceptable latency for live audio/video. A dropped UDP packet causes a brief glitch; a TCP retransmission causes the stream to freeze waiting for the packet.

WebRTC only falls back to TCP (TURN TCP relay) when UDP is completely blocked by firewall. Our config (`iceTransportPolicy: 'all'`) allows this fallback but prefers UDP.

### STUN vs TURN

- **STUN** (used here): Discover your public IP/port. Free. Only helps with NAT traversal for most home/office setups.
- **TURN** (not included, but can be added): Full media relay through a server. Required for symmetric NAT and some corporate firewalls. Costs bandwidth on the relay server.

---

## 14. ENVIRONMENT VARIABLES (.env)

| Variable        | Example Value          | Used By                         |
| --------------- | ---------------------- | ------------------------------- |
| `SECRET_KEY`    | `s3cr3t_r4ndom_str1ng` | Flask session signing           |
| `DB_HOST`       | `localhost`            | MySQL connection                |
| `DB_USER`       | `root`                 | MySQL connection                |
| `DB_PASSWORD`   | `mypassword`           | MySQL connection                |
| `DB_NAME`       | `chitchat`             | MySQL connection                |
| `MAIL_USERNAME` | `youremail@gmail.com`  | Flask-Mail (OTP sender)         |
| `MAIL_PASSWORD` | `gmailapppassword`     | Flask-Mail (Gmail App Password) |
| `PORT`          | `5000` (optional)      | Gunicorn/app listen port        |

---

## 15. KNOWN PATTERNS & IDIOMS

### Room Naming Convention

Chat rooms: `chat_{min(user1_id, user2_id)}_{max(user1_id, user2_id)}`  
Call rooms: `call_{min(user1_id, user2_id)}_{max(user1_id, user2_id)}`  
Private rooms: `str(user_id)` (for per-user events like call notifications, read receipts)

### Base64 Media Upload

Large files (voice, image, video) are sent as base64-encoded data URLs through Socket.IO. The server decodes them:

```python
header, encoded = content.split(",", 1)
file_binary = base64.b64decode(encoded)
```

This is simple but not ideal for very large files (increases payload ~33%). For production, consider multipart HTTP upload.

### isSending / sendCooldown Lock

Two flags prevent accidental double-sends:

- `isSending`: set true immediately, reset after 300ms (network send window)
- `sendCooldown`: set true immediately, reset after 600ms (UI cooldown)

### socket.off() Before Registering Listeners

`socket.off()` is called before all `socket.on()` calls to prevent listener accumulation on page (re-)load. This is important because Flask-SocketIO reconnects on the same page.

---

## 16. PRODUCTION CONSIDERATIONS

1. **HTTPS is mandatory** — WebRTC `getUserMedia()` requires a secure context
2. **Upgrade password hashing** — SHA-256 is not suitable for production. Use `bcrypt` or `argon2`
3. **Restrict CORS** — set `cors_allowed_origins` to your domain only
4. **Use a TURN server** — for users behind symmetric NAT (coturn is a popular open-source option)
5. **Session store** — use Redis for Flask sessions in multi-worker deployments
6. **Database pooling** — replace per-request `get_db()` with SQLAlchemy connection pooling
7. **File storage** — use object storage (S3, Cloudflare R2) instead of local `static/uploads/`
8. **Rate limiting** — protect register/login routes with Flask-Limiter
9. **Message encryption** — consider E2E encryption for messages at rest

---

## 17. DEPENDENCIES

```
flask                     # Web framework
flask-socketio            # WebSocket + Socket.IO support
flask-mail                # Email via SMTP
mysql-connector-python    # MySQL database driver
python-dotenv             # .env file loading
gevent                    # Async worker (required for Socket.IO)
gevent-websocket          # WebSocket support for gevent
gunicorn                  # WSGI production server
```

Frontend (CDN, no npm):

```
socket.io@4.7.2           # Client Socket.IO (from cdn.socket.io)
Plus Jakarta Sans         # Font (from fonts.googleapis.com)
```

---

## 18. FULL EVENT FLOW — NEW USER FIRST CALL

Step-by-step walkthrough of Alice calling Bob for the first time:

1. Alice is logged in as user ID 5. Bob is logged in as user ID 12.
2. Both are on `/chat`. Both connected to Socket.IO. Both in `online_users`.
3. Alice opens a chat with Bob. `currentReceiver = 12`.
4. Alice clicks the audio call button (🔊).
5. `initiateCall('audio')` runs:
   - Emits `incoming_call_notification { caller:5, callee:12, room:'chat_5_12', type:'audio' }` to server
   - Server relays it to Bob's private room `"12"`
6. Alice's browser opens `call.html?myId=5&peerId=12&isCaller=true&type=audio` in a popup.
7. Bob's `socket.on('incoming_call_notification')` fires → `showIncomingCall()` shows the overlay with ring tone.
8. **In Alice's call.html:**
   - `init()` runs, sets call type to audio, hides local video PIP
   - `loadPeerInfo()` fetches Bob's name from `/recent_chats`
   - `setState('ringing')` — "Calling…" + outgoing ring tone starts
   - `getMedia()` — gets microphone access
   - `socket.emit('join_call_room', { room:'call_5_12' })`
   - Server joins Alice to the call room, emits `peer_ready` to others (no one yet)
9. Bob clicks **Accept** → `acceptIncomingCall()`:
   - `hideIncomingCall()` — overlay + ring tone stop
   - Opens `call.html?myId=12&peerId=5&isCaller=false&type=audio` in a popup
10. **In Bob's call.html:**
    - `init()` runs similarly
    - `getMedia()` — gets microphone access
    - `socket.emit('join_call_room', { room:'call_5_12' })` — Bob joins
    - Server emits `peer_ready { userId:12 }` to Alice (who's already in the room)
11. **Alice receives `peer_ready`:**
    - `createPC()` — creates RTCPeerConnection with STUN config
    - `setState('connecting')` — ring tone stops
    - `pc.createOffer()` → `pc.setLocalDescription(offer)`
    - `socket.emit('call_offer', { room:'call_5_12', sdp:offer })`
    - Server relays `call_offer` to Bob
12. **Bob receives `call_offer`:**
    - `createPC()` — creates RTCPeerConnection
    - `setState('connecting')`
    - `pc.setRemoteDescription(offer)` → `remoteDescSet = true`
    - Flush any buffered ICE candidates
    - `pc.createAnswer()` → `pc.setLocalDescription(answer)`
    - `socket.emit('call_answer', { room:'call_5_12', sdp:answer })`
    - Server relays `call_answer` to Alice
13. **Alice receives `call_answer`:**
    - `pc.setRemoteDescription(answer)` → `remoteDescSet = true`
    - Flush buffered ICE candidates
14. **ICE negotiation (both sides):**
    - `pc.onicecandidate` fires as STUN resolves public endpoints
    - Each candidate is emitted via `ice_candidate` through the server
    - Each side calls `pc.addIceCandidate()` to register remote candidates
    - ICE selects the best UDP path (typically host candidate on LAN, srflx on WAN)
15. **Connection established:**
    - `pc.onconnectionstatechange` fires `'connected'`
    - `pc.ontrack` fires → remote audio stream available
    - Both sides call `setState('active')` — timer starts, ring tone long gone
16. **Call proceeds:** Audio flows directly P2P over SRTP/UDP. Server is no longer in the media path at all.
17. **Alice hangs up:** Clicks End Call button → `endCall(true)`:
    - `socket.emit('call_ended', { room:'call_5_12' })`
    - Local stream tracks stopped, RTCPeerConnection closed
    - `setState('ended')` — "Call Ended 0:47" overlay, window closes in 3.2s
18. **Bob receives `call_ended`:**
    - `endCall(false)` — same cleanup without emitting again
    - Window closes in 3.2s

---

_End of Context — Chit Chat v1.0_
