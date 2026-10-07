const homeView = document.querySelector("#home-view");
const roomView = document.querySelector("#room-view");
const createForm = document.querySelector("#create-form");
const joinForm = document.querySelector("#join-form");
const codeInput = document.querySelector("#room-code");
const messageList = document.querySelector("#message-list");
const messageForm = document.querySelector("#message-form");
const messageInput = document.querySelector("#message-input");
const fileInput = document.querySelector("#file-input");
const toast = document.querySelector("#toast");
const themeToggle = document.querySelector("#theme-toggle");
const fileModeNotice = document.querySelector("#file-mode-notice");

let roomSession = null;
let lastMessageId = 0;
let pollTimer = null;
let toastTimer = null;

function applyTheme(theme) {
  const isDark = theme === "dark";
  document.documentElement.dataset.theme = isDark ? "dark" : "light";
  themeToggle.setAttribute("aria-pressed", String(isDark));
  themeToggle.setAttribute("aria-label", `Switch to ${isDark ? "light" : "dark"} mode`);
  themeToggle.querySelector(".theme-icon").textContent = isDark ? "☀" : "☾";
  themeToggle.querySelector(".theme-label").textContent = isDark ? "Light" : "Dark";
  document.querySelector('meta[name="theme-color"]').content = isDark ? "#171b18" : "#f7f7f4";
}

applyTheme(localStorage.getItem("recroom-theme") || "light");
themeToggle.addEventListener("click", () => {
  const nextTheme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  localStorage.setItem("recroom-theme", nextTheme);
  applyTheme(nextTheme);
});

const openedFromFile = location.protocol === "file:";
if (openedFromFile) {
  fileModeNotice.classList.remove("hidden");
  document.querySelectorAll("#create-form input, #create-form button, #join-form input, #join-form button, #message-form input, #message-form button")
    .forEach((control) => { control.disabled = true; });
}

function showError(id, message) {
  document.querySelector(id).textContent = message;
}

async function request(path, options = {}) {
  const response = await fetch(path, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || "Something went wrong. Please try again.");
  return payload;
}

async function enterRoom(path, payload) {
  const result = await request(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  openRoom(result);
}

function openRoom(session) {
  roomSession = session;
  lastMessageId = 0;
  sessionStorage.setItem("recroom-session", JSON.stringify(session));
  homeView.classList.add("hidden");
  roomView.classList.remove("hidden");
  document.querySelector("#room-title-code").textContent = session.code;
  document.querySelector("#room-code-display").textContent = session.code;
  document.querySelector("#end-room-button").classList.toggle("hidden", !session.is_host);
  messageList.replaceChildren();
  pollRoom();
  clearInterval(pollTimer);
  pollTimer = setInterval(pollRoom, 2500);
}

function closeRoom() {
  clearInterval(pollTimer);
  pollTimer = null;
  roomSession = null;
  sessionStorage.removeItem("recroom-session");
  roomView.classList.add("hidden");
  homeView.classList.remove("hidden");
}

function formatTime(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function makeMessage(message) {
  const article = document.createElement("article");
  article.className = "message";
  article.dataset.id = message.id;
  const avatar = document.createElement("div");
  avatar.className = "message-avatar";
  avatar.textContent = message.author.slice(0, 1).toUpperCase();
  const content = document.createElement("div");
  content.className = "message-content";
  const meta = document.createElement("div");
  meta.className = "message-meta";
  const author = document.createElement("span");
  author.className = "message-name";
  author.textContent = message.author;
  const time = document.createElement("time");
  time.className = "message-time";
  time.dateTime = message.created_at;
  time.textContent = formatTime(message.created_at);
  meta.append(author, time);
  content.append(meta);
  if (message.text) {
    const text = document.createElement("p");
    text.className = "message-text";
    text.textContent = message.text;
    content.append(text);
  }
  if (message.attachment) content.append(makeAttachment(message.attachment));
  article.append(avatar, content);
  return article;
}

function makeAttachment(attachment) {
  const url = attachment.url;
  if (attachment.type.startsWith("image/")) {
    const image = document.createElement("img");
    image.className = "message-attachment image";
    image.src = url;
    image.alt = `Shared photo: ${attachment.name}`;
    image.loading = "lazy";
    return image;
  }
  if (attachment.type.startsWith("video/")) {
    const video = document.createElement("video");
    video.className = "message-attachment video";
    video.src = url;
    video.controls = true;
    video.preload = "metadata";
    return video;
  }
  if (attachment.type.startsWith("audio/")) {
    const audio = document.createElement("audio");
    audio.className = "message-attachment";
    audio.src = url;
    audio.controls = true;
    audio.preload = "metadata";
    return audio;
  }
  const link = document.createElement("a");
  link.className = "file-link";
  link.href = url;
  link.textContent = `Open ${attachment.name}`;
  link.target = "_blank";
  link.rel = "noopener";
  return link;
}

function renderMembers(members) {
  const list = document.querySelector("#people-list");
  list.replaceChildren();
  for (const member of members) {
    const row = document.createElement("div");
    row.className = "person-row";
    const avatar = document.createElement("span");
    avatar.className = "person-avatar";
    avatar.textContent = member.name.slice(0, 1).toUpperCase();
    const info = document.createElement("span");
    info.className = "person-info";
    const name = document.createElement("strong");
    name.textContent = member.name;
    const status = document.createElement("span");
    status.textContent = member.is_host ? "bringing everyone together" : "here for the good stuff";
    info.append(name, status);
    row.append(avatar, info);
    if (member.is_host) {
      const badge = document.createElement("span");
      badge.className = "host-tag";
      badge.textContent = "HOST";
      row.append(badge);
    }
    list.append(row);
  }
  const count = members.length;
  document.querySelector("#member-count").textContent = `${count} ${count === 1 ? "here" : "here"}`;
}

async function pollRoom() {
  if (!roomSession) return;
  const session = roomSession;
  try {
    const params = new URLSearchParams({ member: session.token, after: String(lastMessageId) });
    const result = await request(`/api/rooms/${encodeURIComponent(session.code)}/sync?${params}`);
    if (roomSession !== session) return;
    renderMembers(result.members);
    if (result.messages.length) {
      const empty = messageList.querySelector(".empty-chat");
      if (empty) empty.remove();
      const fragment = document.createDocumentFragment();
      for (const message of result.messages) {
        fragment.append(makeMessage(message));
        lastMessageId = Math.max(lastMessageId, message.id);
      }
      messageList.append(fragment);
      messageList.scrollTop = messageList.scrollHeight;
    } else if (!lastMessageId && !messageList.children.length) {
      const empty = document.createElement("p");
      empty.className = "empty-chat";
      empty.textContent = "This space is yours. Start us off with a hello.";
      messageList.append(empty);
    }
  } catch (error) {
    if (roomSession === session) {
      showToast(error.message);
      if (error.message.includes("session has ended") || error.message.includes("space has ended")) closeRoom();
    }
  }
}

function showToast(message) {
  toast.textContent = message;
  toast.classList.add("visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("visible"), 2600);
}

async function copyText(text, success) {
  try {
    await navigator.clipboard.writeText(text);
    showToast(success);
  } catch {
    showToast("Couldn't copy automatically — select and copy the space code.");
  }
}

async function sendMessage(event) {
  event.preventDefault();
  const text = messageInput.value.trim();
  if (!text || !roomSession) return;
  try {
    await request(`/api/rooms/${encodeURIComponent(roomSession.code)}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ member: roomSession.token, text }),
    });
    messageInput.value = "";
    await pollRoom();
  } catch (error) {
    showToast(error.message);
  }
}

async function uploadFile(file) {
  if (!roomSession || !file) return;
  if (file.size > 12 * 1024 * 1024) {
    showToast("That file is a little too big. Keep it under 12 MB.");
    return;
  }
  const data = new FormData();
  data.append("member", roomSession.token);
  data.append("file", file);
  showToast("Sharing with the space…");
  try {
    await request(`/api/rooms/${encodeURIComponent(roomSession.code)}/uploads`, { method: "POST", body: data });
    await pollRoom();
    showToast("Shared with everyone in the space.");
  } catch (error) {
    showToast(error.message);
  } finally {
    fileInput.value = "";
  }
}

async function leaveRoom() {
  if (!roomSession) return;
  const session = roomSession;
  try {
    await request(`/api/rooms/${encodeURIComponent(session.code)}/leave`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ member: session.token }),
    });
  } catch (error) {
    showToast(error.message);
  } finally {
    closeRoom();
  }
}

createForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  showError("#create-error", "");
  const button = createForm.querySelector("button[type=submit]");
  button.disabled = true;
  try {
    await enterRoom("/api/rooms", { name: createForm.elements.name.value.trim() });
  } catch (error) {
    showError("#create-error", error.message);
  } finally {
    button.disabled = false;
  }
});

joinForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  showError("#join-error", "");
  const button = joinForm.querySelector("button[type=submit]");
  button.disabled = true;
  try {
    await enterRoom("/api/rooms/join", {
      name: joinForm.elements.name.value.trim(),
      code: joinForm.elements.code.value.trim().toUpperCase(),
    });
  } catch (error) {
    showError("#join-error", error.message);
  } finally {
    button.disabled = false;
  }
});

codeInput.addEventListener("input", () => {
  codeInput.value = codeInput.value.toUpperCase().replace(/[^A-Z2-9]/g, "").slice(0, 6);
});
messageForm.addEventListener("submit", sendMessage);
document.querySelector("#attach-button").addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => uploadFile(fileInput.files[0]));
document.querySelector("#leave-button").addEventListener("click", leaveRoom);
document.querySelector("#copy-code-button").addEventListener("click", () => copyText(roomSession.code, "Space code copied!"));
const copyInvite = () => copyText(`${location.origin}/?space=${roomSession.code}`, "Invite link copied — send it to your people.");
document.querySelector("#share-button").addEventListener("click", copyInvite);
document.querySelector("#invite-button").addEventListener("click", copyInvite);
document.querySelector("#end-room-button").addEventListener("click", async () => {
  if (!roomSession || !confirm("End this space for everyone? Shared messages and files will be removed.")) return;
  const session = roomSession;
  try {
    await request(`/api/rooms/${encodeURIComponent(session.code)}/end`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ member: session.token }),
    });
    closeRoom();
    showToast("Space ended. Start another one whenever you're ready.");
  } catch (error) {
    showToast(error.message);
  }
});

const inviteParams = new URLSearchParams(location.search);
const invitedCode = inviteParams.get("space") || inviteParams.get("room");
if (invitedCode && !openedFromFile) {
  codeInput.value = invitedCode.toUpperCase().replace(/[^A-Z2-9]/g, "").slice(0, 6);
  document.querySelector("#guest-name").focus();
}
try {
  const savedSession = JSON.parse(sessionStorage.getItem("recroom-session"));
  if (!openedFromFile && savedSession?.code && savedSession?.token) openRoom(savedSession);
} catch {
  sessionStorage.removeItem("recroom-session");
}
