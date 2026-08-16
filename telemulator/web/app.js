const accounts = [];
let current = null;
let openPeer = null;
let openChatObj = null;
const pending = new Map();
let eventsSource = null;
const STORE = "telemulator_accounts";
const DEBUG_ROWS = 500;

function saveAccounts() {
  localStorage.setItem(STORE, JSON.stringify(accounts));
}

// A corrupt value in storage must not take the whole client down: without catch
// the exception flies before the first paint and the tab stays blank, debug
// console included — you would have to fix it by hand in devtools.
function savedAccounts() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORE) || "[]");
    return Array.isArray(saved) ? saved : [];
  } catch {
    return [];
  }
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  const ct = res.headers.get("content-type") || "";
  return ct.includes("json") ? res.json() : res;
}

function formError(form, err) {
  let el = form.querySelector(".form-error");
  if (!el) {
    el = document.createElement("p");
    el.className = "form-error";
    form.append(el);
  }
  if (!err) {
    el.hidden = true;
    el.textContent = "";
    return;
  }
  el.hidden = false;
  const raw = String(err.message || err);
  let text = raw;
  const brace = raw.indexOf("{");
  if (brace >= 0) {
    try {
      const body = JSON.parse(raw.slice(brace));
      if (typeof body.detail === "string") text = body.detail;
    } catch {
      // FastAPI returns JSON; if not, leave "400 {…}" as-is.
    }
  }
  el.textContent = text;
}

async function withForm(form, run) {
  formError(form, null);
  try {
    await run();
  } catch (err) {
    formError(form, err);
  }
}

function renderAccounts() {
  const sel = document.getElementById("account");
  sel.replaceChildren();
  for (const a of accounts) {
    // The name comes from the operator — textContent only, as everywhere in this file.
    const opt = new Option(`${a.first_name} (${a.id})`, String(a.id));
    opt.selected = current !== null && a.id === current.id;
    sel.append(opt);
  }
}

function bubble(msg, myId) {
  const mine = msg.from && msg.from.id === myId;
  const el = document.createElement("div");
  el.className = "bubble" + (mine ? " mine" : "");
  const text = document.createElement("div");
  if (msg.new_chat_members) {
    text.textContent = msg.new_chat_members.map((u) => u.first_name).join(", ");
  } else if (msg.left_chat_member) {
    text.textContent = msg.left_chat_member.first_name;
  } else {
    text.textContent = msg.text || msg.caption || "";
  }
  el.appendChild(text);
  if (!msg.from && msg.sender_chat) {
    const who = document.createElement("div");
    who.textContent = msg.sender_chat.title;
    el.prepend(who);
  }
  if (msg.photo) {
    const img = document.createElement("img");
    img.src = "/user/files/" + msg.photo[0].file_id + ".bin";
    el.appendChild(img);
  }
  if (msg.document) {
    const doc = document.createElement("div");
    doc.className = "doc";
    doc.textContent = msg.document.file_name || "file";
    el.appendChild(doc);
  }
  const markup = (msg.reply_markup && msg.reply_markup.inline_keyboard) || [];
  if (markup.length) {
    const kb = document.createElement("div");
    kb.className = "inline-kb";
    for (const row of markup) {
      const r = document.createElement("div");
      for (const btn of row) {
        if (btn.url) {
          const a = document.createElement("a");
          a.href = btn.url;
          a.target = "_blank";
          a.rel = "noopener";
          a.textContent = btn.text;
          r.appendChild(a);
        } else {
          const b = document.createElement("button");
          b.textContent = btn.text;
          b.dataset.data = btn.callback_data;
          b.dataset.mid = msg.message_id;
          b.addEventListener("click", onInline);
          r.appendChild(b);
        }
      }
      kb.appendChild(r);
    }
    el.appendChild(kb);
  }
  return el;
}

async function loadChats() {
  const { chats } = await api("/user/chats");
  const ul = document.getElementById("chats");
  ul.innerHTML = "";
  for (const chat of chats) {
    const li = document.createElement("li");
    li.textContent = chat.title || chat.first_name || String(chat.id);
    li.dataset.peer = chat.id;
    li.addEventListener("click", () => openChat(chat));
    ul.appendChild(li);
  }
}

async function openChat(chat) {
  openPeer = chat.id;
  document.getElementById("chat-head").textContent = chat.title || chat.first_name || chat.id;
  const data = await api("/user/chats/" + chat.id + "/messages");
  const feed = document.getElementById("feed");
  feed.innerHTML = "";
  for (const msg of data.messages) feed.appendChild(bubble(msg, current.id));
  feed.scrollTop = feed.scrollHeight;
  renderReply(data.reply_keyboard);
  openChatObj = chat;
  const membersBox = document.getElementById("members");
  const composer = document.getElementById("composer");
  if (!chat.type || chat.type === "private") {
    membersBox.hidden = true;
    composer.hidden = false;
    return;
  }
  membersBox.hidden = false;
  const members = await api("/user/chats/" + chat.id + "/members");
  const ul = document.getElementById("member-list");
  ul.replaceChildren();
  for (const m of members.members) {
    const li = document.createElement("li");
    const role = m.status === "creator" ? "creator" : m.status === "administrator" ? "admin" : "member";
    li.textContent = (m.user.first_name || m.user.id) + " · " + role;
    if (m.user.id !== current.id) {
      const kick = document.createElement("button");
      kick.type = "button";
      kick.textContent = "Kick";
      kick.addEventListener("click", async () => {
        await withForm(document.getElementById("add-member"), async () => {
          await api("/user/chats/" + chat.id + "/members/" + m.user.id, { method: "DELETE" });
          await openChat(chat);
        });
      });
      li.appendChild(kick);
    }
    ul.appendChild(li);
  }
  composer.hidden = !canPost(chat, current.id, members.members);
}

function canPost(chat, meId, members) {
  if (!chat || chat.type !== "channel") return true;
  const me = (members || []).find((m) => m.user && m.user.id === meId);
  if (!me) return false;
  return me.status === "creator" || (me.status === "administrator" && me.can_post_messages);
}

function renderReply(rows) {
  const box = document.getElementById("reply-kb");
  box.innerHTML = "";
  if (!rows) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  for (const row of rows) {
    const r = document.createElement("div");
    for (const label of row) {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = label;
      b.addEventListener("click", () => {
        withForm(document.getElementById("composer"), () => sendText(label));
      });
      r.appendChild(b);
    }
    box.appendChild(r);
  }
}

async function sendText(text) {
  if (!openPeer || !text) return;
  await api("/user/chats/" + openPeer + "/messages", {
    method: "POST",
    body: JSON.stringify({ text }),
  });
  const chat = openChatObj || { id: openPeer, first_name: document.getElementById("chat-head").textContent };
  await openChat(chat);
}

async function onInline(ev) {
  const btn = ev.currentTarget;
  btn.classList.add("spin");
  let queryId = null;
  try {
    const res = await api(
      "/user/chats/" + openPeer + "/messages/" + btn.dataset.mid + "/press",
      { method: "POST", body: JSON.stringify({ data: btn.dataset.data }) }
    );
    queryId = res.query_id;
    pending.set(queryId, btn);
  } finally {
    if (!queryId) btn.classList.remove("spin");
  }
}

function resetOpenChat() {
  openPeer = null;
  openChatObj = null;
  pending.clear();
  document.getElementById("feed").innerHTML = "";
  document.getElementById("chat-head").textContent = "Select a chat";
  document.getElementById("members").hidden = true;
  document.getElementById("composer").hidden = false;
  renderReply(null);
}

function connectEvents() {
  if (eventsSource) eventsSource.close();
  eventsSource = new EventSource("/user/events");
  eventsSource.onmessage = (ev) => {
    const data = JSON.parse(ev.data);
    if (data.type === "reset") {
      accounts.length = 0;
      current = null;
      localStorage.removeItem(STORE);
      renderAccounts();
      resetOpenChat();
      document.getElementById("chats").innerHTML = "";
      loadJournal();
      return;
    }
    if (data.type === "message" || data.type === "message_edited") {
      if (current && data.viewer_id === current.id) {
        loadChats();
        if (data.peer_id === openPeer) {
          openChat(openChatObj || { id: openPeer, first_name: document.getElementById("chat-head").textContent });
        }
      }
    }
    if (data.type === "callback_answered") {
      const btn = pending.get(data.query_id);
      pending.delete(data.query_id);
      if (btn) btn.classList.remove("spin");
    }
    if (data.type === "journal") renderJournal(data.record);
  };
  loadJournal();
}

async function loadJournal() {
  const data = await api("/admin/journal");
  document.getElementById("debug-calls").innerHTML = "";
  document.getElementById("debug-holes").innerHTML = "";
  // A hole sits in both journal queues: calls and unimplemented.
  // We do not draw it twice, and we do not drop it — unimplemented keeps a deeper history.
  for (const rec of data.calls) {
    if (rec.kind !== "unimplemented") renderJournal(rec);
  }
  for (const rec of data.unimplemented) renderJournal(rec);
}

function renderJournal(record) {
  const hole = record.kind !== "ok";
  const row = document.createElement("div");
  row.className = hole ? "j-unimplemented" : "j-" + record.kind;
  const count = record.response && record.response.result_count;
  if (record.method === "getUpdates" && count != null) {
    row.textContent = `getUpdates result_count=${count}`;
  } else {
    row.textContent = `${record.status || ""} ${record.method} ${JSON.stringify(record.params)} → ${JSON.stringify(record.response)}`;
  }
  const box = document.getElementById(hole ? "debug-holes" : "debug-calls");
  box.prepend(row);
  // The journal on the server is capped at 500 records, the DOM is not: an hour
  // of polling fills the tab with thousands of getUpdates rows and it starts to lag.
  while (box.childElementCount > DEBUG_ROWS) box.lastElementChild.remove();
}

document.getElementById("new-user").addEventListener("submit", async (e) => {
  e.preventDefault();
  await withForm(e.target, async () => {
    const first_name = e.target.first_name.value;
    const user = (await api("/admin/users", { method: "POST", body: JSON.stringify({ first_name }) })).user;
    await api("/user/sessions", { method: "POST", body: JSON.stringify({ user_id: user.id }) });
    accounts.push(user);
    saveAccounts();
    current = user;
    renderAccounts();
    resetOpenChat();
    await loadChats();
    connectEvents();
  });
});

document.getElementById("new-bot").addEventListener("submit", async (e) => {
  e.preventDefault();
  await withForm(e.target, async () => {
    const token = e.target.token.value;
    await api("/admin/bots", {
      method: "POST",
      body: JSON.stringify({ token, first_name: e.target.first_name.value }),
    });
    if (current) {
      await api("/admin/dialogs", {
        method: "POST",
        body: JSON.stringify({ user_id: current.id, bot_token: token }),
      });
      await loadChats();
    }
  });
});

function parseIds(raw) {
  return String(raw || "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean)
    .map(Number);
}

document.getElementById("new-group").addEventListener("submit", async (e) => {
  e.preventDefault();
  await withForm(e.target, async () => {
    await api("/user/chats", {
      method: "POST",
      body: JSON.stringify({
        type: "supergroup",
        title: e.target.title.value,
        member_ids: parseIds(e.target.member_ids.value),
      }),
    });
    await loadChats();
  });
});

document.getElementById("new-channel").addEventListener("submit", async (e) => {
  e.preventDefault();
  await withForm(e.target, async () => {
    await api("/user/chats", {
      method: "POST",
      body: JSON.stringify({
        type: "channel",
        title: e.target.title.value,
        member_ids: parseIds(e.target.member_ids.value),
      }),
    });
    await loadChats();
  });
});

document.getElementById("add-member").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!openChatObj) return;
  await withForm(e.target, async () => {
    const body = { user_id: Number(e.target.user_id.value) };
    if (document.getElementById("add-as-admin").checked) body.status = "administrator";
    await api("/user/chats/" + openChatObj.id + "/members", {
      method: "POST",
      body: JSON.stringify(body),
    });
    await openChat(openChatObj);
  });
});

document.getElementById("account").addEventListener("change", async (e) => {
  const id = Number(e.target.value);
  current = accounts.find((a) => a.id === id);
  await api("/user/sessions", { method: "POST", body: JSON.stringify({ user_id: id }) });
  resetOpenChat();
  await loadChats();
  connectEvents();
});

document.getElementById("composer").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = document.getElementById("draft");
  const text = input.value;
  input.value = "";
  formError(e.target, null);
  try {
    await sendText(text);
  } catch (err) {
    input.value = text;
    formError(e.target, err);
  }
});

// The session lives in a cookie on the server, the list of created people only here,
// so after F5 we restore accounts from localStorage and the current one from /user/me.
async function restore() {
  accounts.push(...savedAccounts());
  let me = null;
  try {
    me = await api("/user/me");
  } catch {
    me = null;
  }
  if (me === null) {
    accounts.length = 0;
    saveAccounts();
    renderAccounts();
    await loadJournal();
    return;
  }
  if (!accounts.some((a) => a.id === me.id)) accounts.push(me);
  current = accounts.find((a) => a.id === me.id);
  saveAccounts();
  renderAccounts();
  resetOpenChat();
  await loadChats();
  connectEvents();
}

restore();
