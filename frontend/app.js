const state = { offset: 0, limit: 50, hasMore: false, articles: [], taxonomy: [], selected: null, loading: false, listMode: "all", selectedSection: "", study: { active: false, ids: [], index: -1 }, editingReference: null, username: "" };
const $ = (selector) => document.querySelector(selector);
const listView = $("#list-view");
const readerView = $("#reader-view");
const listElement = $("#article-list");
const feedStatus = $("#feed-status");
const loadMoreButton = $("#load-more");
let searchTimer;
let authMode = "login";
let appEventsBound = false;
let recoveryCodeRequested = false;

function escapeHtml(value = "") {
  return String(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
}

function isUrlOnlyText(value = "") {
  return /^https?:\/\/\S+$/i.test(String(value).trim());
}

function localDateString(date = new Date()) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function formatDate(value) {
  if (!value) return "DATE NOT SET";
  return new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", year: "numeric" }).format(new Date(value));
}

function formatPublishedTime(value) {
  if (!value) return "Date unavailable";
  return new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", year: "numeric" }).format(new Date(value));
}

function setStatus(message, isError = false) {
  feedStatus.textContent = message;
  feedStatus.classList.toggle("error", isError);
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const detail = (await response.json()).detail;
      if (typeof detail === "string") message = detail;
      else if (Array.isArray(detail)) message = detail.map((item) => {
        const field = Array.isArray(item.loc) ? item.loc[item.loc.length - 1] : "";
        return `${field ? `${field}: ` : ""}${item.msg || "Invalid value"}`;
      }).join("; ");
      else if (detail) message = JSON.stringify(detail);
    } catch { /* Keep the status message. */ }
    throw new Error(message);
  }
  return response.json();
}

function setAuthMode(mode) {
  authMode = mode;
  const signingUp = mode === "signup" || mode === "signup_verify";
  const forgot = mode === "forgot" || mode === "reset";
  const verifyingSignup = mode === "signup_verify";
  const resetting = mode === "reset";
  $("#auth-mode-tabs").hidden = forgot || verifyingSignup;
  $("#auth-return-row").hidden = !(forgot || verifyingSignup);
  $("#auth-return").textContent = verifyingSignup ? "Back to sign up" : "Back to sign in";
  $("#auth-login-tab").classList.toggle("active", !signingUp);
  $("#auth-signup-tab").classList.toggle("active", signingUp);
  $("#auth-login-tab").setAttribute("aria-pressed", String(!signingUp));
  $("#auth-signup-tab").setAttribute("aria-pressed", String(signingUp));
  $("#auth-heading").innerHTML = signingUp
    ? (verifyingSignup ? 'Verify your email<span class="brand-period">.</span>' : 'Create your account<span class="brand-period">.</span>')
    : (forgot ? (resetting ? 'Choose a new password<span class="brand-period">.</span>' : 'Reset your password<span class="brand-period">.</span>') : 'Sign in<span class="brand-period">.</span>');
  $("#auth-description").textContent = signingUp
    ? (verifyingSignup ? "Enter the eight-digit code we sent to your email." : "Create an account with your email. Add a username if you want one.")
    : (forgot ? (resetting ? "Enter the code from your email and choose a new password." : "Enter your account email to request a reset code.") : "Sign in with your email to continue to your PrepBrief reading desk.");
  $("#auth-submit").textContent = ({ login: "Sign in", signup: "Create account", signup_verify: "Verify email", forgot: "Send reset code", reset: "Reset password" })[mode];
  $("#auth-password").autocomplete = signingUp || resetting ? "new-password" : "current-password";
  $("#auth-password").minLength = signingUp || resetting ? 10 : 1;
  $("#auth-password").required = !forgot || resetting;
  $("#auth-password").closest("label").hidden = verifyingSignup || mode === "forgot";
  $("#auth-password-label").firstChild.textContent = resetting ? "New password" : "Password";
  $("#auth-confirm-password-label").hidden = !resetting;
  $("#auth-confirm-password").required = resetting;
  $("#auth-confirm-password").minLength = resetting ? 10 : 0;
  $("#auth-username-label").hidden = mode !== "signup";
  $("#auth-username").required = false;
  $("#auth-email").required = true;
  $("#auth-email").readOnly = verifyingSignup || resetting;
  $("#auth-code-label").hidden = !(verifyingSignup || resetting);
  $("#auth-code").required = verifyingSignup || resetting;
  $("#forgot-password-row").hidden = mode !== "login";
  $("#auth-footnote").textContent = signingUp
    ? (verifyingSignup ? "The code expires after 10 minutes." : "Choose a password with at least 10 characters. Your data stays private to your account.")
    : (forgot ? "If the account has a verified email, a code will be sent." : "New to PrepBrief? Choose Sign up to create your account.");
}

function showAuth(accountExists, message = "") {
  document.body.classList.remove("auth-pending");
  document.body.classList.add("auth-required");
  $("#auth-loading").hidden = true;
  $("#auth-view").hidden = false;
  listView.hidden = true;
  readerView.hidden = true;
  $("#reference-view").hidden = true;
  $("#ask-view").hidden = true;
  $("#recovery-view").hidden = true;
  $("#app-nav").hidden = true;
  document.querySelector(".topbar-note").hidden = true;
  document.querySelectorAll(".topbar > .docs-link").forEach((link) => { link.hidden = true; });
  setAuthMode(accountExists ? "login" : "signup");
  $("#auth-message").textContent = message;
}

async function openReadingDesk() {
  document.body.classList.remove("auth-pending");
  $("#auth-loading").hidden = true;
  document.body.classList.remove("auth-required");
  $("#auth-view").hidden = true;
  listView.hidden = false;
  $("#app-nav").hidden = false;
  document.querySelector(".topbar-note").hidden = false;
  document.querySelectorAll(".topbar > .docs-link").forEach((link) => { link.hidden = false; });
  if (!appEventsBound) {
    bindEvents();
    appEventsBound = true;
  }
  try {
    state.taxonomy = await fetchJson("/taxonomy");
    populateTaxonomy();
  } catch (error) {
    setStatus(`Couldn’t load taxonomy: ${error.message}`, true);
  }
  await loadArticles();
}

async function submitAuth(event) {
  event.preventDefault();
  const button = $("#auth-submit");
  const message = $("#auth-message");
  button.disabled = true;
  button.textContent = "Please wait…";
  message.textContent = "";
  try {
    const email = $("#auth-email").value.trim();
    const username = $("#auth-username").value.trim();
    const code = $("#auth-code").value.trim();
    const options = (body) => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    let result;
    if (authMode === "login") result = await fetchJson("/auth/login", options({ email, password: $("#auth-password").value }));
    else if (authMode === "signup") {
      result = await fetchJson("/auth/signup", options({ username: username || null, email, password: $("#auth-password").value }));
      setAuthMode("signup_verify");
      message.textContent = result.message;
      return;
    } else if (authMode === "signup_verify") result = await fetchJson("/auth/signup/verify", options({ email, code }));
    else if (authMode === "forgot") {
      result = await fetchJson("/auth/password/forgot", options({ email }));
      setAuthMode("reset");
      message.textContent = result.message;
      return;
    } else {
      const newPassword = $("#auth-password").value;
      const confirmPassword = $("#auth-confirm-password").value;
      if (newPassword !== confirmPassword) throw new Error("The new passwords do not match.");
      result = await fetchJson("/auth/password/reset", options({ email, code, new_password: newPassword, confirm_password: confirmPassword }));
      setAuthMode("login");
      $("#auth-password").value = "";
      $("#auth-confirm-password").value = "";
      $("#auth-code").value = "";
      message.textContent = result.message;
      return;
    }
    $("#auth-password").value = "";
    $("#auth-code").value = "";
    state.username = result.username || state.username;
    await openReadingDesk();
  } catch (error) {
    message.textContent = error.message;
  } finally {
    button.disabled = false;
    button.textContent = ({ login: "Sign in", signup: "Create account", signup_verify: "Verify email", forgot: "Send reset code", reset: "Reset password" })[authMode];
  }
}

async function logOut() {
  try {
    await fetchJson("/auth/logout", { method: "POST" });
    state.selected = null;
    state.articles = [];
    state.taxonomy = [];
    state.study = { active: false, ids: [], index: -1 };
    listElement.innerHTML = "";
    $("#reader-content").innerHTML = "";
    $("#reference-list").innerHTML = "";
    $("#ask-result").innerHTML = "";
    showAuth(true);
  } catch (error) {
    setStatus(`Couldn’t log out: ${error.message}`, true);
  }
}

async function submitRecoveryEmail(event) {
  event.preventDefault();
  const button = $("#recovery-submit");
  const message = $("#recovery-message");
  const email = $("#recovery-email").value.trim();
  button.disabled = true;
  try {
    const endpoint = recoveryCodeRequested ? "/auth/email/change/verify" : "/auth/email/change/request";
    const body = { email };
    if (recoveryCodeRequested) body.code = $("#recovery-code").value.trim();
    const response = await fetchJson(endpoint, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    message.textContent = response.message;
    recoveryCodeRequested = !recoveryCodeRequested;
    $("#recovery-code-label").hidden = !recoveryCodeRequested;
    $("#recovery-code").required = recoveryCodeRequested;
    button.textContent = recoveryCodeRequested ? "Verify and save email" : "Send verification code";
    if (!recoveryCodeRequested) $("#recovery-code").value = "";
  } catch (error) {
    message.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

function selectedParams() {
  const params = new URLSearchParams({ limit: String(state.limit), offset: String(state.offset) });
  const values = {
    date: state.listMode === "all" ? $("#date-filter").value : "",
    subject: $("#subject-filter").value,
    topic: $("#topic-filter").value,
    section: state.selectedSection,
    must_read: $("#must-read-filter").value,
    saved: state.listMode === "saved" ? "true" : "",
    q: $("#search").value.trim(),
  };
  for (const [key, value] of Object.entries(values)) if (value) params.set(key, value);
  return params;
}

async function loadArticles(append = false) {
  if (state.loading) return;
  state.loading = true;
  if (!append) {
    state.offset = 0;
    state.articles = [];
    listElement.innerHTML = "";
  }
  loadMoreButton.disabled = true;
  setStatus(append ? "Loading more…" : "Loading your articles…");
  try {
    const params = selectedParams();
    const articles = await fetchJson(`/articles?${params}`);
    state.articles = append ? [...state.articles, ...articles] : articles;
    state.offset = state.articles.length;
    state.hasMore = articles.length === state.limit;
    renderArticles();
    updateSectionPills();
    const countLabel = state.hasMore ? `${state.articles.length}+` : String(state.articles.length);
    $("#result-count").textContent = countLabel;
    setStatus(state.articles.length ? `${state.articles.length}${state.hasMore ? "+" : ""} articles` : "No articles match these filters");
    loadMoreButton.hidden = !state.hasMore;
    $("#saved-count").textContent = state.listMode === "saved" ? `${state.articles.length}${state.hasMore ? "+" : ""}` : "";
    if (!append) updateDaySummary();
  } catch (error) {
    listElement.innerHTML = `<div class="empty-state"><div class="empty-icon">↻</div><strong>Couldn’t load your feed</strong><span>${escapeHtml(error.message)}</span></div>`;
    $("#result-count").textContent = "—";
    setStatus("Connection error", true);
    loadMoreButton.hidden = true;
  } finally {
    state.loading = false;
    loadMoreButton.disabled = false;
  }
}

function updateSectionPills() {
  document.querySelectorAll(".section-pill").forEach((button) => {
    const active = button.dataset.section === state.selectedSection;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
}

async function updateDaySummary() {
  const panel = $("#day-summary");
  const selectedDay = $("#date-filter").value;
  if (state.listMode !== "all" || !selectedDay) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  $("#summary-label").textContent = selectedDay === localDateString() ? "TIME LEFT TODAY" : `TIME LEFT · ${formatDate(selectedDay).toUpperCase()}`;
  try {
    const summary = await fetchJson(`/day/${selectedDay}/summary`);
    const total = Number(summary.article_count);
    const read = Number(summary.read_count);
    $("#time-left").textContent = `${Number(summary.time_left_minutes)} min`;
    $("#summary-caption").textContent = total ? `${read} of ${total} articles read` : "No articles for this date";
    $("#progress-fill").style.width = total ? `${Math.min(100, (read / total) * 100)}%` : "0%";
  } catch (error) {
    $("#time-left").textContent = "— min";
    $("#summary-caption").textContent = `Couldn’t load reading progress: ${error.message}`;
  }
}

function setListMode(mode) {
  state.listMode = mode;
  const saved = mode === "saved";
  $("#all-tab").classList.toggle("active", !saved);
  $("#saved-tab").classList.toggle("active", saved);
  $("#all-tab").setAttribute("aria-pressed", String(!saved));
  $("#saved-tab").setAttribute("aria-pressed", String(saved));
  $("#date-filter").disabled = saved;
  $("#page-title").innerHTML = saved ? 'Saved notebook<span class="brand-period">.</span>' : 'Today’s brief<span class="brand-period">.</span>';
  $(".section-kicker").textContent = saved ? "YOUR PERSONAL COLLECTION" : "IN YOUR FEED";
  $("#results-title").textContent = saved ? "Saved articles" : "Articles";
  loadArticles();
}

function renderArticles() {
  if (!state.articles.length) {
    listElement.innerHTML = '<div class="empty-state"><div class="empty-icon">✳</div><strong>A little breathing room.</strong><span>Try a different date or clear a filter to see more articles.</span></div>';
    return;
  }
  listElement.innerHTML = state.articles.map((article) => {
    const subjects = (article.subjects || []).map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("");
    const topics = (article.topics || []).slice(0, 3).map((tag) => `<span class="tag topic-tag">${escapeHtml(tag)}</span>`).join("");
    const mustRead = article.must_read ? '<span class="must-badge">MUST READ</span>' : "";
    const read = article.is_read ? '<span class="read-badge">✓ Read</span>' : "";
    const saved = article.is_saved ? '<span class="saved-badge">SAVED</span>' : "";
    const summary = article.summary ? `<p class="card-summary">${escapeHtml(article.summary)}</p>` : "";
    const time = article.has_text && article.reading_minutes ? `${article.reading_minutes} MIN READ` : "TEXT NOT ADDED";
    return `<button class="article-card" type="button" data-id="${Number(article.id)}">
      <div class="card-top"><span class="card-source"><span class="source-mark">${escapeHtml((article.source || "P").slice(0, 1))}</span>${escapeHtml(article.source || "Unknown source")} · ${escapeHtml(article.section || "News")}</span><span class="card-badges">${mustRead}${saved}${read}</span></div>
      <h3 class="card-title">${escapeHtml(article.title)}</h3>${summary}
      <div class="card-bottom"><div class="tag-list">${subjects}${topics}</div><span class="card-time">${escapeHtml(time)}</span></div>
    </button>`;
  }).join("");
  listElement.querySelectorAll(".article-card").forEach((card) => card.addEventListener("click", () => openArticle(Number(card.dataset.id))));
}

function populateTaxonomy() {
  const subjectSelect = $("#subject-filter");
  for (const subject of state.taxonomy) {
    const option = document.createElement("option");
    option.value = subject.name;
    option.textContent = subject.name;
    subjectSelect.append(option);
  }
  updateTopics();
}

function updateTopics() {
  const selectedSubject = $("#subject-filter").value;
  const topicSelect = $("#topic-filter");
  topicSelect.innerHTML = '<option value="">All topics</option>';
  const subjects = selectedSubject ? state.taxonomy.filter((item) => item.name === selectedSubject) : state.taxonomy;
  const topics = subjects.flatMap((item) => item.topics || []);
  const unique = [...new Map(topics.map((topic) => [topic.name, topic])).values()];
  for (const topic of unique) {
    const option = document.createElement("option");
    option.value = topic.name;
    option.textContent = topic.name;
    topicSelect.append(option);
  }
  topicSelect.disabled = unique.length === 0;
}

function highlightedText(article) {
  const savedText = article.full_text || "";
  const looksLikeUrl = isUrlOnlyText(savedText);
  const text = looksLikeUrl ? "" : savedText;
  const fetchAction = article.url ? `<div class="fetch-article-action"><button id="fetch-article-text" class="read-action" type="button">${text ? "Replace text from source link" : "Fetch text from source link"}</button><p id="fetch-article-message" role="status"></p></div>` : "";
  const editor = `<details class="article-text-editor"><summary>${text ? "Replace article text or retry analysis" : "Add article text"}</summary><p class="side-empty">Paste text you have access to. PrepBrief saves it, asks Gemini to tag it, and creates embeddings.</p><form id="article-text-form" class="article-text-form"><label for="article-text-input">Article text</label><textarea id="article-text-input" maxlength="300000" required placeholder="Paste the article text here…">${text ? escapeHtml(text) : ""}</textarea><button class="read-action" type="submit">Save &amp; analyze with Gemini</button><p id="article-text-message" role="status"></p></form></details>`;
  if (!text) return `<div class="no-text">${looksLikeUrl ? "A link was saved in the article text field, so it is not being shown as article text. Use Fetch text from source link below." : "RSS provides this headline and link, but not the article body. Try fetching from the public source link, or add text below."}</div>${fetchAction}${editor}`;
  const ranges = [];
  let cursor = 0;
  for (const item of article.highlights || []) {
    if (!item.text) continue;
    const index = text.indexOf(item.text, cursor);
    if (index >= 0) {
      ranges.push([index, index + item.text.length]);
      cursor = index + item.text.length;
    }
  }
  if (!ranges.length) return `<div class="article-text">${escapeHtml(text)}</div>${fetchAction}${editor}`;
  let result = "", position = 0;
  for (const [start, end] of ranges) {
    if (start < position) continue;
    result += `${escapeHtml(text.slice(position, start))}<mark>${escapeHtml(text.slice(start, end))}</mark>`;
    position = end;
  }
  result += escapeHtml(text.slice(position));
  return `<div class="article-text">${result}</div>${fetchAction}${editor}`;
}

async function openArticle(id) {
  setStatus("Opening article…");
  try {
    const article = await fetchJson(`/articles/${id}`);
    state.selected = article;
    renderReader(article);
    listView.hidden = true;
    readerView.hidden = false;
    window.scrollTo({ top: 0, behavior: "smooth" });
  } catch (error) {
    setStatus(error.message, true);
  }
}

function renderReader(article) {
  const hasArticleText = Boolean(article.full_text && !isUrlOnlyText(article.full_text));
  const readingTime = hasArticleText && article.reading_minutes ? `${Number(article.reading_minutes)} MIN READ` : "TEXT NOT ADDED";
  const articleStatus = hasArticleText ? (article.status || "new").replaceAll("_", " ").toUpperCase() : "LINK SAVED";
  const subjects = (article.subjects || []).map((subject) => `<span class="tag">${escapeHtml(subject)}</span>`).join("");
  const topics = (article.topics || []).map((topic) => `<span class="tag topic-tag">${escapeHtml(topic.name)}</span>`).join("");
  const takeaways = (article.key_takeaways || []).map((item) => `<li>${escapeHtml(item.text)}</li>`).join("");
  const mustRead = article.must_read ? '<span class="must-badge">MUST READ</span>' : "";
  const readButton = article.is_read
    ? '<button class="read-action" type="button" disabled>✓ Marked as read</button>'
    : '<button id="mark-read" class="read-action" type="button">Mark as read</button>';
  const saveButton = article.is_saved
    ? '<button id="toggle-save" class="read-action saved-action" type="button">✓ Saved in notebook</button>'
    : '<button id="toggle-save" class="read-action" type="button">Save to notebook</button>';
  const notes = (article.notes || []).map((note) => `<li><p>${escapeHtml(note.text)}</p><time>${escapeHtml(formatPublishedTime(note.created_at))} · ${note.embedding_indexed ? "in Ask my notes" : "not indexed"}</time></li>`).join("");
  const corrections = (article.corrections || []).map((correction) => {
    const before = (correction.original_analysis.tags || []).map((tag) => `${tag.subject} / ${tag.topic}`).join(", ") || "No tags";
    const after = (correction.corrected_analysis.tags || []).map((tag) => `${tag.subject} / ${tag.topic}`).join(", ") || "No tags";
    const note = correction.note ? `<p>${escapeHtml(correction.note)}</p>` : "";
    return `<li><time>${escapeHtml(formatPublishedTime(correction.created_at))}</time><div>${escapeHtml(before)} → ${escapeHtml(after)}</div>${note}</li>`;
  }).join("");
  const safeUrl = /^https?:\/\//i.test(article.url || "") ? article.url : "";
  const sourceLink = safeUrl ? `<a class="source-link" href="${escapeHtml(safeUrl)}" target="_blank" rel="noopener noreferrer">Open original article ↗</a>` : "";
  const studyBar = state.study.active
    ? `<div class="study-bar"><span><strong>Study mode</strong> · ${state.study.index + 1} of ${state.study.ids.length}</span><div class="study-controls"><button id="study-prev" type="button" ${state.study.index <= 0 ? "disabled" : ""}>← Previous</button><button id="study-next" type="button" ${state.study.index >= state.study.ids.length - 1 ? "disabled" : ""}>Next →</button><button id="exit-study" class="study-exit" type="button">Exit</button></div></div>`
    : "";
  $("#reader-content").innerHTML = `
    ${studyBar}
    <header class="reader-header">
      <div class="reader-meta"><span>${escapeHtml(article.source || "News")}</span><span class="separator">/</span><span>${escapeHtml(article.section || "Current affairs")}</span><span class="separator">/</span><span>${escapeHtml(formatPublishedTime(article.published_at))}</span>${mustRead}</div>
      <h1 class="reader-title">${escapeHtml(article.title)}</h1>
      <div class="reader-meta"><span>${readingTime}</span><span class="separator">·</span><span>${escapeHtml(articleStatus)}</span></div>
      ${article.summary ? `<p class="reader-summary">${escapeHtml(article.summary)}</p>` : ""}
      <div class="reader-tags">${subjects}${topics}</div>
    </header>
    <div class="reader-layout">
      <article class="reader-body"><h2>Article</h2>${highlightedText(article)}</article>
      <aside class="reader-side">
        <section class="side-card"><h3>Key takeaways</h3>${takeaways ? `<ol class="takeaway-list">${takeaways}</ol>` : '<p class="side-empty">No takeaways are available yet.</p>'}</section>
        <section class="side-card read-card"><h3>Reading progress</h3>${readButton}<p class="side-empty" style="margin-top:10px">${article.is_read ? "This article is in your completed reading." : "Mark this article read when you finish."}</p></section>
        <section class="side-card"><h3>Notebook</h3>${saveButton}</section>
        ${state.study.active ? "" : '<section class="side-card"><h3>Study mode</h3><p class="side-empty">Move through the selected day’s articles one at a time.</p><button id="start-study" class="read-action study-start" type="button">Study this day →</button></section>'}
        <section class="side-card notes-card"><h3>Your notes</h3><form id="note-form"><label class="sr-only" for="note-text">Add a note</label><textarea id="note-text" maxlength="20000" placeholder="Write a thought to remember…" required></textarea><button class="read-action note-submit" type="submit">Add note</button></form><ul class="notes-list">${notes || '<li class="notes-empty">No notes yet. Add one above.</li>'}</ul></section>
        <section class="side-card"><h3>Source</h3>${sourceLink || '<p class="side-empty">Original link unavailable.</p>'}</section>
      </aside>
    </div>
    <section id="related-section" class="related-section" hidden><div class="related-heading"><span class="section-kicker">SEMANTIC MATCH + SHARED TAGS</span><h2>Related articles</h2></div><div id="related-list" class="related-list"></div></section>
    <details class="correction-panel">
      <summary>Correct this classification</summary>
      <p class="correction-help">Your correction updates this article and is saved as a private example to improve future tagging.</p>
      <form id="correction-form">
        <div class="correction-fields">
          <label>Relevant to UPSC?<select id="correction-relevant"><option value="true">Yes</option><option value="false">No</option></select></label>
          <label class="must-read-choice"><input id="correction-must-read" type="checkbox"> Mark as must read</label>
        </div>
        <div class="correction-tag-heading"><strong>Subject and topic tags</strong><button id="add-correction-tag" class="text-button" type="button">+ Add tag</button></div>
        <div id="correction-tags" class="correction-tags"></div>
        <label class="correction-note-label" for="correction-note">Why is this correction needed? <span>Optional</span></label>
        <textarea id="correction-note" maxlength="2000" placeholder="A short note helps identify patterns later…"></textarea>
        <div class="correction-submit-row"><button id="submit-correction" class="read-action" type="submit">Save correction</button><span id="correction-message" role="status"></span></div>
      </form>
      ${corrections ? `<div class="correction-history"><h3>Previous corrections</h3><ul>${corrections}</ul></div>` : ""}
    </details>`;
  const markRead = $("#mark-read");
  if (markRead) markRead.addEventListener("click", () => markArticleRead(article.id));
  const saveButtonElement = $("#toggle-save");
  if (saveButtonElement) saveButtonElement.addEventListener("click", () => toggleSaved(article.id));
  const noteForm = $("#note-form");
  if (noteForm) noteForm.addEventListener("submit", (event) => addNote(event, article.id));
  const startStudy = $("#start-study");
  if (startStudy) startStudy.addEventListener("click", () => beginStudy(article.id));
  const prev = $("#study-prev");
  if (prev) prev.addEventListener("click", () => moveStudy(-1));
  const next = $("#study-next");
  if (next) next.addEventListener("click", () => moveStudy(1));
  const exitStudy = $("#exit-study");
  if (exitStudy) exitStudy.addEventListener("click", exitStudyMode);
  initializeCorrectionForm(article);
  const textForm = $("#article-text-form");
  if (textForm) textForm.addEventListener("submit", (event) => saveArticleText(event, article.id));
  const fetchTextButton = $("#fetch-article-text");
  if (fetchTextButton) fetchTextButton.addEventListener("click", () => fetchArticleText(article));
  loadRelated(article.id);
}

async function fetchArticleText(article) {
  const hasArticleBody = article.full_text && !isUrlOnlyText(article.full_text);
  if (hasArticleBody && !window.confirm("This replaces the text currently saved for this article and regenerates its analysis. Continue?")) return;
  const button = $("#fetch-article-text");
  const message = $("#fetch-article-message");
  button.disabled = true;
  button.textContent = "Fetching and analyzing…";
  message.textContent = "Trying the public article page. Some sources allow only a preview.";
  try {
    const result = await fetchJson(`/articles/${article.id}/fetch-text`, { method: "POST" });
    const updated = await fetchJson(`/articles/${article.id}`);
    state.selected = updated;
    await loadArticles();
    renderReader(updated);
    const notice = document.createElement("p");
    notice.className = "article-analysis-notice";
    notice.textContent = `Fetched ${result.fetched_words} words. Gemini tagging ${result.tagged ? `finished (${result.tag_status})` : `failed (${result.tag_error})`}; embeddings ${result.embedded ? `created (${result.embedding_chunks} chunk(s))` : `failed (${result.embedding_error})`}.`;
    $(".reader-body").append(notice);
  } catch (error) {
    button.disabled = false;
    button.textContent = hasArticleBody ? "Replace text from source link" : "Fetch text from source link";
    message.textContent = error.message;
  }
}

async function saveArticleText(event, articleId) {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector("button[type=submit]");
  const message = $("#article-text-message");
  button.disabled = true;
  button.textContent = "Saving and analyzing…";
  message.textContent = "Gemini is processing the text you provided. This can take a little while.";
  try {
    const result = await fetchJson(`/articles/${articleId}/text`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: $("#article-text-input").value }),
    });
    const messages = [`Saved (${result.reading_minutes} min read).`];
    messages.push(result.tagged ? `Gemini tagging: ${result.tag_status}.` : `Tagging could not finish (${result.tag_error}).`);
    messages.push(result.embedded ? `Created ${result.embedding_chunks} embedding chunk(s).` : `Embedding could not finish (${result.embedding_error}).`);
    message.textContent = messages.join(" ");
    const updated = await fetchJson(`/articles/${articleId}`);
    state.selected = updated;
    await loadArticles();
    renderReader(updated);
    const notice = document.createElement("p");
    notice.className = "article-analysis-notice";
    notice.textContent = messages.join(" ");
    $(".reader-body").append(notice);
  } catch (error) {
    button.disabled = false;
    button.textContent = "Save & analyze with Gemini";
    message.textContent = error.message;
  }
}

async function loadRelated(articleId) {
  const section = $("#related-section");
  if (!section) return;
  try {
    const items = await fetchJson(`/articles/${articleId}/related?limit=4`);
    section.hidden = items.length === 0;
    $("#related-list").innerHTML = items.map((item) => `
      <button class="related-card" type="button" data-id="${Number(item.id)}">
        <span class="related-meta">${escapeHtml(item.source)} · ${item.similarity ? `${Number(item.similarity).toFixed(2)} semantic match · ` : ""}${Number(item.shared_topic_count)} shared tag${Number(item.shared_topic_count) === 1 ? "" : "s"}</span>
        <strong>${escapeHtml(item.title)}</strong>
        <span class="related-tags">${(item.shared_topics || []).map(escapeHtml).join(" · ")}</span>
      </button>`).join("");
    $("#related-list").querySelectorAll(".related-card").forEach((button) => {
      button.addEventListener("click", () => openArticle(Number(button.dataset.id)));
    });
  } catch {
    section.hidden = true;
  }
}

function addReferenceTagRow(subjectName = "", topicName = "") {
  const row = document.createElement("div");
  row.className = "reference-tag-row";
  const subject = document.createElement("select");
  subject.innerHTML = '<option value="">Choose subject</option>';
  subject.setAttribute("aria-label", "Reference subject");
  for (const item of state.taxonomy) {
    const option = document.createElement("option");
    option.value = item.name;
    option.textContent = item.name;
    subject.append(option);
  }
  subject.value = subjectName;
  const topic = document.createElement("select");
  topic.setAttribute("aria-label", "Reference topic");
  const fillTopics = (preferred = "") => {
    topic.innerHTML = '<option value="">Choose topic</option>';
    const selected = state.taxonomy.find((item) => item.name === subject.value);
    for (const item of selected?.topics || []) {
      const option = document.createElement("option");
      option.value = item.name;
      option.textContent = item.name;
      topic.append(option);
    }
    topic.disabled = !selected;
    topic.value = preferred;
  };
  fillTopics(topicName);
  subject.addEventListener("change", () => fillTopics());
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "remove-correction-tag";
  remove.setAttribute("aria-label", "Remove taxonomy tag");
  remove.textContent = "×";
  remove.addEventListener("click", () => row.remove());
  row.append(subject, topic, remove);
  $("#reference-tags").append(row);
}

async function loadReferences() {
  const q = $("#reference-search").value.trim();
  const params = q ? `?q=${encodeURIComponent(q)}` : "";
  try {
    const references = await fetchJson(`/references${params}`);
    $("#reference-count").textContent = String(references.length);
    $("#reference-list").innerHTML = references.length ? references.map((item) => {
      const tags = (item.tags || []).map((tag) => `<span class="tag">${escapeHtml(tag.subject)}</span><span class="tag topic-tag">${escapeHtml(tag.topic)}</span>`).join("");
      const link = /^https?:\/\//i.test(item.source_url || "") ? `<a href="${escapeHtml(item.source_url)}" target="_blank" rel="noopener noreferrer">Official source ↗</a>` : "";
      const indexed = item.embedding_indexed ? "Indexed for Ask" : "Not indexed";
      return `<article class="reference-card"><div class="reference-card-top"><span class="must-badge">${escapeHtml(item.category)}</span><span class="reference-index-status">${indexed}</span></div><h3>${escapeHtml(item.title)}</h3><p>${escapeHtml(item.summary)}</p><div class="tag-list">${tags}</div><div class="reference-card-footer"><span>${link}</span><div><button type="button" class="text-button edit-reference" data-id="${Number(item.id)}">Edit</button><button type="button" class="text-button delete-reference" data-id="${Number(item.id)}">Delete</button></div></div></article>`;
    }).join("") : '<div class="empty-state"><div class="empty-icon">⌂</div><strong>Your reference desk is ready.</strong><span>Add your own concise notes about schemes, judgements, committees, or reports.</span></div>';
    $("#reference-list").querySelectorAll(".edit-reference").forEach((button) => button.addEventListener("click", () => editReference(Number(button.dataset.id))));
    $("#reference-list").querySelectorAll(".delete-reference").forEach((button) => button.addEventListener("click", () => deleteReference(Number(button.dataset.id))));
  } catch (error) {
    $("#reference-list").innerHTML = `<div class="empty-state"><strong>Couldn’t load references</strong><span>${escapeHtml(error.message)}</span></div>`;
  }
}

function resetReferenceForm() {
  state.editingReference = null;
  $("#reference-form").reset();
  $("#reference-tags").innerHTML = "";
  $("#reference-form-title").textContent = "New library entry";
  $("#save-reference").textContent = "Save reference";
  $("#cancel-reference-edit").hidden = true;
}

async function editReference(id) {
  try {
    const item = await fetchJson(`/references/${id}`);
    state.editingReference = id;
    $("#reference-title").value = item.title;
    $("#reference-category").value = item.category;
    $("#reference-summary").value = item.summary;
    $("#reference-url").value = item.source_url || "";
    $("#reference-tags").innerHTML = "";
    (item.tags || []).forEach((tag) => addReferenceTagRow(tag.subject, tag.topic));
    $("#reference-form-title").textContent = "Edit library entry";
    $("#save-reference").textContent = "Update reference";
    $("#cancel-reference-edit").hidden = false;
    $("#reference-form").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    $("#reference-message").textContent = error.message;
  }
}

async function deleteReference(id) {
  if (!window.confirm("Delete this reference entry?")) return;
  try {
    await fetchJson(`/references/${id}`, { method: "DELETE" });
    await loadReferences();
  } catch (error) {
    $("#reference-message").textContent = error.message;
  }
}

async function saveReference(event) {
  event.preventDefault();
  const message = $("#reference-message");
  const tags = [];
  for (const row of document.querySelectorAll(".reference-tag-row")) {
    const subject = row.querySelector("select").value;
    const topic = row.querySelectorAll("select")[1].value;
    if (subject && topic) tags.push({ subject, topic });
    else if (subject || topic) {
      message.textContent = "Choose both a subject and topic, or remove the blank row.";
      return;
    }
  }
  const payload = {
    title: $("#reference-title").value.trim(),
    category: $("#reference-category").value.trim(),
    summary: $("#reference-summary").value.trim(),
    source_url: $("#reference-url").value.trim() || null,
    tags,
  };
  const button = $("#save-reference");
  button.disabled = true;
  button.textContent = "Saving…";
  message.textContent = "";
  try {
    const url = state.editingReference ? `/references/${state.editingReference}` : "/references";
    const method = state.editingReference ? "PUT" : "POST";
    const result = await fetchJson(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    resetReferenceForm();
    message.textContent = result.embedding_indexed ? "Saved and indexed for Ask my notes." : "Saved. Embedding unavailable; run the note indexing command later.";
    await loadReferences();
  } catch (error) {
    message.textContent = error.message;
  } finally {
    button.disabled = false;
    if (state.editingReference) button.textContent = "Update reference";
    else if (button.textContent === "Saving…") button.textContent = "Save reference";
  }
}

async function askMyNotes(event) {
  event.preventDefault();
  const question = $("#ask-question").value.trim();
  const button = $("#ask-submit");
  const resultElement = $("#ask-result");
  button.disabled = true;
  button.textContent = "Searching notes…";
  resultElement.innerHTML = '<p class="ask-loading">Finding relevant notes and references…</p>';
  try {
    const result = await fetchJson("/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, top_k: 5 }),
    });
    const sources = result.sources.map((source) => {
      const safeUrl = /^https?:\/\//i.test(source.url || "") ? source.url : "";
      const title = safeUrl ? `<a href="${escapeHtml(safeUrl)}" target="_blank" rel="noopener noreferrer">${escapeHtml(source.title)} ↗</a>` : escapeHtml(source.title);
      return `<li><span>[${Number(source.number)}]</span> ${title}<small>${escapeHtml(source.type)} · similarity ${Number(source.similarity).toFixed(2)}</small></li>`;
    }).join("");
    resultElement.innerHTML = `<article class="answer-card"><span class="section-kicker">GEMINI · GROUNDED ANSWER</span><div class="answer-text">${escapeHtml(result.answer).replaceAll("\n", "<br>")}</div>${sources ? `<div class="answer-sources"><h3>Sources</h3><ol>${sources}</ol></div>` : '<p class="side-empty">No source references were returned.</p>'}</article>`;
  } catch (error) {
    resultElement.innerHTML = `<div class="empty-state"><strong>Couldn’t answer yet</strong><span>${escapeHtml(error.message)}. Add a note/reference or index imported article text with pipeline.index_articles.</span></div>`;
  } finally {
    button.disabled = false;
    button.textContent = "Ask Gemini →";
  }
}

function showUtility(id) {
  listView.hidden = true;
  readerView.hidden = true;
  $("#reference-view").hidden = id !== "reference-view";
  $("#ask-view").hidden = id !== "ask-view";
  $("#recovery-view").hidden = id !== "recovery-view";
  state.selected = null;
  state.study = { active: false, ids: [], index: -1 };
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function addCorrectionTagRow(subjectName = "", topicName = "") {
  const row = document.createElement("div");
  row.className = "correction-tag-row";
  const subject = document.createElement("select");
  subject.className = "correction-subject";
  subject.setAttribute("aria-label", "Subject");
  subject.innerHTML = '<option value="">Choose subject</option>';
  for (const item of state.taxonomy) {
    const option = document.createElement("option");
    option.value = item.name;
    option.textContent = item.name;
    subject.append(option);
  }
  subject.value = subjectName;

  const topic = document.createElement("select");
  topic.className = "correction-topic";
  topic.setAttribute("aria-label", "Topic");
  const fillTopics = (preferred = "") => {
    const found = state.taxonomy.find((item) => item.name === subject.value);
    topic.innerHTML = '<option value="">Choose topic</option>';
    for (const item of found?.topics || []) {
      const option = document.createElement("option");
      option.value = item.name;
      option.textContent = item.name;
      topic.append(option);
    }
    topic.disabled = !found;
    topic.value = preferred;
  };
  fillTopics(topicName);
  subject.addEventListener("change", () => fillTopics());

  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "remove-correction-tag";
  remove.setAttribute("aria-label", "Remove tag");
  remove.textContent = "×";
  remove.addEventListener("click", () => row.remove());
  row.append(subject, topic, remove);
  $("#correction-tags").append(row);
}

function initializeCorrectionForm(article) {
  const form = $("#correction-form");
  if (!form) return;
  $("#correction-relevant").value = article.is_relevant === false ? "false" : "true";
  $("#correction-must-read").checked = Boolean(article.must_read);
  const tags = article.topics || [];
  if (tags.length) tags.forEach((tag) => addCorrectionTagRow(tag.subject, tag.name));
  else addCorrectionTagRow();
  $("#add-correction-tag").addEventListener("click", () => addCorrectionTagRow());
  $("#correction-relevant").addEventListener("change", (event) => {
    const inactive = event.target.value === "false";
    if (inactive) {
      $("#correction-must-read").checked = false;
      $("#correction-tags").innerHTML = "";
    } else if (!$("#correction-tags").children.length) {
      addCorrectionTagRow();
    }
  });
  form.addEventListener("submit", (event) => submitCorrection(event, article.id));
}

async function submitCorrection(event, articleId) {
  event.preventDefault();
  const message = $("#correction-message");
  const rows = [...document.querySelectorAll(".correction-tag-row")];
  const tags = [];
  for (const row of rows) {
    const subject = row.querySelector(".correction-subject").value;
    const topic = row.querySelector(".correction-topic").value;
    if (subject && topic) tags.push({ subject, topic });
    else if (subject || topic) {
      message.textContent = "Choose both a subject and topic, or remove the blank row.";
      return;
    }
  }
  const isRelevant = $("#correction-relevant").value === "true";
  const payload = {
    is_relevant: isRelevant,
    must_read: isRelevant && $("#correction-must-read").checked,
    tags,
    note: $("#correction-note").value.trim() || null,
  };
  const button = $("#submit-correction");
  button.disabled = true;
  button.textContent = "Saving…";
  message.textContent = "";
  try {
    await fetchJson(`/articles/${articleId}/corrections`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const updated = await fetchJson(`/articles/${articleId}`);
    state.selected = updated;
    await loadArticles();
    renderReader(updated);
  } catch (error) {
    button.disabled = false;
    button.textContent = "Save correction";
    message.textContent = error.message;
  }
}

async function markArticleRead(id) {
  const button = $("#mark-read");
  if (!button) return;
  button.disabled = true;
  button.textContent = "Saving…";
  try {
    await fetchJson(`/articles/${id}/read`, { method: "POST" });
    state.selected.is_read = true;
    const listed = state.articles.find((article) => article.id === id);
    if (listed) listed.is_read = true;
    renderReader(state.selected);
    renderArticles();
    updateDaySummary();
  } catch (error) {
    button.disabled = false;
    button.textContent = "Mark as read";
    const notice = document.createElement("p");
    notice.className = "reader-notice";
    notice.textContent = error.message;
    button.after(notice);
  }
}

async function toggleSaved(id) {
  const article = state.selected;
  if (!article) return;
  const button = $("#toggle-save");
  button.disabled = true;
  button.textContent = "Saving…";
  try {
    const result = await fetchJson(`/articles/${id}/save`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ is_saved: !article.is_saved }),
    });
    article.is_saved = result.is_saved;
    const listed = state.articles.find((item) => item.id === id);
    if (listed) listed.is_saved = result.is_saved;
    renderReader(article);
    renderArticles();
  } catch (error) {
    button.disabled = false;
    button.textContent = error.message;
  }
}

async function addNote(event, id) {
  event.preventDefault();
  const input = $("#note-text");
  const text = input.value.trim();
  if (!text) return;
  const submit = $(".note-submit");
  submit.disabled = true;
  submit.textContent = "Saving…";
  try {
    const note = await fetchJson(`/articles/${id}/notes`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    state.selected.notes = [note, ...(state.selected.notes || [])];
    renderReader(state.selected);
  } catch (error) {
    submit.disabled = false;
    submit.textContent = "Add note";
    const notice = document.createElement("p");
    notice.className = "reader-notice";
    notice.textContent = error.message;
    submit.after(notice);
  }
}

async function beginStudy(articleId) {
  const articleDay = state.selected?.published_at?.slice(0, 10);
  const selectedDay = state.listMode === "saved" ? (articleDay || localDateString()) : ($("#date-filter").value || articleDay || localDateString());
  const button = $("#start-study");
  button.disabled = true;
  button.textContent = "Preparing…";
  try {
    const dailyArticles = await fetchJson(`/articles?date=${encodeURIComponent(selectedDay)}&limit=200&offset=0`);
    const ids = dailyArticles.map((article) => article.id);
    const index = ids.indexOf(articleId);
    if (index < 0) throw new Error("This article isn’t in the selected day’s feed.");
    state.study = { active: true, ids, index };
    renderReader(state.selected);
  } catch (error) {
    button.disabled = false;
    button.textContent = error.message;
  }
}

async function moveStudy(step) {
  const nextIndex = state.study.index + step;
  if (nextIndex < 0 || nextIndex >= state.study.ids.length) return;
  state.study.index = nextIndex;
  await openArticle(state.study.ids[nextIndex]);
}

function exitStudyMode() {
  state.study = { active: false, ids: [], index: -1 };
  if (state.selected) renderReader(state.selected);
}

function resetFilters() {
  $("#search").value = "";
  if (state.listMode === "all") $("#date-filter").value = localDateString();
  $("#subject-filter").value = "";
  $("#must-read-filter").value = "";
  updateTopics();
  $("#topic-filter").value = "";
  state.selectedSection = "";
  updateSectionPills();
  loadArticles();
}

function showList() {
  $("#reference-view").hidden = true;
  $("#ask-view").hidden = true;
  $("#recovery-view").hidden = true;
  readerView.hidden = true;
  listView.hidden = false;
  state.selected = null;
  state.study = { active: false, ids: [], index: -1 };
  if (state.listMode === "saved") loadArticles();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function bindEvents() {
  for (const id of ["#date-filter", "#topic-filter", "#must-read-filter"]) $(id).addEventListener("change", () => loadArticles());
  $("#subject-filter").addEventListener("change", () => { updateTopics(); loadArticles(); });
  $("#search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => loadArticles(), 300); });
  $("#clear-filters").addEventListener("click", resetFilters);
  document.querySelectorAll(".section-pill").forEach((button) => button.addEventListener("click", () => {
    state.selectedSection = button.dataset.section || "";
    updateSectionPills();
    loadArticles();
  }));
  $("#all-tab").addEventListener("click", () => setListMode("all"));
  $("#saved-tab").addEventListener("click", () => setListMode("saved"));
  loadMoreButton.addEventListener("click", () => loadArticles(true));
  $("#back-button").addEventListener("click", showList);
  $("#logout-button").addEventListener("click", logOut);
  document.querySelectorAll(".utility-back").forEach((button) => button.addEventListener("click", showList));
  $("#open-references").addEventListener("click", () => { showUtility("reference-view"); loadReferences(); });
  $("#open-ask").addEventListener("click", () => showUtility("ask-view"));
  $("#open-recovery").addEventListener("click", () => {
    recoveryCodeRequested = false;
    $("#recovery-code-label").hidden = true;
    $("#recovery-code").required = false;
    $("#recovery-message").textContent = "";
    $("#recovery-email").value = "";
    $("#recovery-submit").textContent = "Send verification code";
    showUtility("recovery-view");
  });
  $("#recovery-email-form").addEventListener("submit", submitRecoveryEmail);
  $("#reference-form").addEventListener("submit", saveReference);
  $("#add-reference-tag").addEventListener("click", () => addReferenceTagRow());
  $("#cancel-reference-edit").addEventListener("click", resetReferenceForm);
  $("#reference-search").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(loadReferences, 250);
  });
  $("#ask-form").addEventListener("submit", askMyNotes);
  document.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      $("#search").focus();
    }
    if (event.key === "Escape" && (!readerView.hidden || !$("#reference-view").hidden || !$("#ask-view").hidden)) showList();
  });
}

async function init() {
  $("#today-label").textContent = new Intl.DateTimeFormat(undefined, { weekday: "long", day: "numeric", month: "long" }).format(new Date());
  $("#date-filter").value = localDateString();
  $("#auth-form").addEventListener("submit", submitAuth);
  $("#auth-login-tab").addEventListener("click", () => setAuthMode("login"));
  $("#auth-signup-tab").addEventListener("click", () => setAuthMode("signup"));
  $("#forgot-password").addEventListener("click", () => {
    setAuthMode("forgot");
    $("#auth-message").textContent = "";
  });
  $("#auth-return").addEventListener("click", () => {
    setAuthMode(authMode === "signup_verify" ? "signup" : "login");
    $("#auth-message").textContent = "";
  });
  try {
    const auth = await fetchJson("/auth/status");
    if (auth.authenticated) { state.username = auth.username || ""; await openReadingDesk(); }
    else showAuth(auth.account_exists);
  } catch (error) {
    showAuth(false, `Couldn’t check sign-in status: ${error.message}`);
  }
}

init();
