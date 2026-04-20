const myId = document.getElementById('mydata').dataset.myid;
let usersList = JSON.parse(document.getElementById('mydata').dataset.users);

// upgrade usersList preview for calls
// ensure recent conversation preview handles call text
function formatPreview(msg) {
  if (!msg) return '';
  if (msg.indexOf('voice/') !== -1) return 'Voice message';
  if (msg.indexOf('chat_') !== -1) return 'Call';
  return msg;
}

const socket = io();
let currentRoom = null;
let currentReceiver = null;

const usersDiv = document.getElementById('users');
const chatBox = document.getElementById('chat-box');
const chatHeader = document.getElementById('chat-header');

function renderUsers(list) {
  usersDiv.innerHTML = '';
  list.forEach(u => {
    const div = document.createElement('div');
    div.classList.add('user');
    div.dataset.id = u.id;
    div.textContent = u.username;
    div.onclick = () => {
      currentReceiver = u.id;
      currentRoom = 'chat_' + Math.min(myId, currentReceiver) + '_' + Math.max(myId, currentReceiver);
      socket.emit('join', { room: currentRoom });
      chatHeader.textContent = `Chat with ${u.username}`;
      loadMessages(u.id);
    };
    usersDiv.appendChild(div);
  });
}

// Initial render
renderUsers(usersList);

// ---------------- Search Users ----------------
document.getElementById('search').addEventListener('input', e => {
  const filter = e.target.value.toLowerCase();
  const filtered = usersList.filter(u => u.username.toLowerCase().includes(filter));
  renderUsers(filtered);
});

// ---------------- Send Message ----------------
document.getElementById('send').onclick = () => {
  if (!currentReceiver) return;
  const message = document.getElementById('msg').value.trim();
  if (!message) return;
  socket.emit('send_message', { sender: myId, receiver: currentReceiver, message });
  document.getElementById('msg').value = '';
};

// helper that renders a message object into the chat box
function appendMessage(data) {
  const div = document.createElement('div');
  const isSent = ((data.sender_id || data.sender) == myId);
  div.className = isSent ? 'msg sent' : 'msg received';

  let content = '';
  if (data.type === 'voice') {
    content = `<audio controls src="${data.message}"></audio>`;
  } else if (data.type === 'image') {
    content = `<img src="${data.message}" onclick="window.open(this.src,'_blank')">`;
  } else if (data.type === 'video') {
    content = `<video controls src="${data.message}"></video>`;
  } else if (data.type === 'call') {
    // show simple line with phone icon
    content = `<svg width="16" height="16" viewBox="0 0 24 24" style="vertical-align:middle; margin-right:4px;"><path d="M6.62 10.79c1.44 2.83 3.76 5.14 6.59 6.59l2.2-2.2c.27-.27.67-.36 1.02-.24 1.12.37 2.33.57 3.57.57.55 0 1 .45 1 1V20c0 .55-.45 1-1 1-9.39 0-17-7.61-17-17 0-.55.45-1 1-1h3.5c.55 0 1 .45 1 1 0 1.25.2 2.45.57 3.57.11.35.03.74-.25 1.02l-2.2 2.2z"/></svg> ${data.message}`;
  } else {
    content = (data.message || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }

  div.innerHTML = `<b>${isSent ? 'You' : 'User'}</b>: ${content}`;
  chatBox.appendChild(div);
  chatBox.scrollTop = chatBox.scrollHeight;
}

// ---------------- Receive Message ----------------
socket.on('receive_message', data => {
  if (!currentRoom) return;
  const expectedRoom = 'chat_' + Math.min(myId, data.receiver) + '_' + Math.max(myId, data.receiver);
  if (expectedRoom !== currentRoom) return;

  appendMessage(data);
});

// ---------------- Load Past Messages ----------------
function loadMessages(otherId) {
  fetch(`/messages/${otherId}`)
    .then(res => res.json())
    .then(msgs => {
      chatBox.innerHTML = '';
      msgs.forEach(m => appendMessage(m));
    });
}
