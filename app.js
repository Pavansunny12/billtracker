const STORAGE_KEY = "bill-collect-v1";
const ringLength = 302;

const starterMembers = [];

const canonicalNamesByPhone = {};

const els = {
  monthSelect: document.querySelector("#monthSelect"),
  syncStatus: document.querySelector("#syncStatus"),
  newMonthButton: document.querySelector("#newMonthButton"),
  monthDialog: document.querySelector("#monthDialog"),
  monthForm: document.querySelector("#monthForm"),
  closeMonthDialogButton: document.querySelector("#closeMonthDialogButton"),
  cancelMonthButton: document.querySelector("#cancelMonthButton"),
  newMonthNameInput: document.querySelector("#newMonthNameInput"),
  newMonthYearInput: document.querySelector("#newMonthYearInput"),
  newMonthBillInput: document.querySelector("#newMonthBillInput"),
  newMonthStatus: document.querySelector("#newMonthStatus"),
  renameMonthButton: document.querySelector("#renameMonthButton"),
  remainingAmount: document.querySelector("#remainingAmount"),
  progressText: document.querySelector("#progressText"),
  progressCircle: document.querySelector("#progressCircle"),
  progressValue: document.querySelector("#progressValue"),
  totalBill: document.querySelector("#totalBill"),
  totalCollected: document.querySelector("#totalCollected"),
  pendingCount: document.querySelector("#pendingCount"),
  workflowMonth: document.querySelector("#workflowMonth"),
  workflowMembers: document.querySelector("#workflowMembers"),
  workflowPending: document.querySelector("#workflowPending"),
  workflowStatus: document.querySelector("#workflowStatus"),
  unpaidOnlyInput: document.querySelector("#unpaidOnlyInput"),
  addMemberButton: document.querySelector("#addMemberButton"),
  exportMonthButton: document.querySelector("#exportMonthButton"),
  memberList: document.querySelector("#memberList"),
  historyMemberSelect: document.querySelector("#historyMemberSelect"),
  historyTotalDue: document.querySelector("#historyTotalDue"),
  historyTotalPaid: document.querySelector("#historyTotalPaid"),
  historyPending: document.querySelector("#historyPending"),
  historyList: document.querySelector("#historyList"),
  pendingList: document.querySelector("#pendingList"),
  markRemindersButton: document.querySelector("#markRemindersButton"),
  messageBox: document.querySelector("#messageBox"),
  copyMessageButton: document.querySelector("#copyMessageButton"),
  copyStatus: document.querySelector("#copyStatus"),
  billFileInput: document.querySelector("#billFileInput"),
  billTextInput: document.querySelector("#billTextInput"),
  scanBillButton: document.querySelector("#scanBillButton"),
  applyBillButton: document.querySelector("#applyBillButton"),
  detectedMonthBox: document.querySelector("#detectedMonthBox"),
  detectedMonthText: document.querySelector("#detectedMonthText"),
  useDetectedMonthButton: document.querySelector("#useDetectedMonthButton"),
  billSuggestionList: document.querySelector("#billSuggestionList"),
  billAuditSummary: document.querySelector("#billAuditSummary"),
  chatFileInput: document.querySelector("#chatFileInput"),
  chatTextInput: document.querySelector("#chatTextInput"),
  scanChatButton: document.querySelector("#scanChatButton"),
  importResults: document.querySelector("#importResults"),
  exportBackupButton: document.querySelector("#exportBackupButton"),
  importBackupInput: document.querySelector("#importBackupInput"),
  memberTemplate: document.querySelector("#memberTemplate")
};

let state = loadState();
let billSuggestions = [];
let billAudit = null;
let detectedBillMonth = "";
let saveTimer = 0;

function makeId() {
  if (window.crypto && typeof window.crypto.randomUUID === "function") {
    return window.crypto.randomUUID();
  }
  return `id-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function money(value) {
  return Number(value || 0).toLocaleString("en-US", {
    style: "currency",
    currency: "USD"
  });
}

function todayValue() {
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function monthLabel(date = new Date()) {
  return new Intl.DateTimeFormat("en-US", {
    month: "long",
    year: "numeric"
  }).format(date);
}

function createMember(member) {
  const digits = last10Digits(member.phone);
  return {
    id: makeId(),
    name: canonicalNamesByPhone[digits] || member.name,
    phone: member.phone,
    due: Number(member.due || 0),
    paid: false,
    received: 0,
    datePaid: "",
    method: "",
    notes: "",
    reminderSent: false
  };
}

function normalizeMember(member) {
  const digits = last10Digits(member.phone);
  return {
    id: member.id || makeId(),
    name: canonicalNamesByPhone[digits] || member.name || "Unnamed",
    phone: member.phone || "",
    due: Math.max(Number(member.due || 0), 0),
    paid: Boolean(member.paid),
    received: Math.max(Number(member.received || 0), 0),
    datePaid: member.datePaid || "",
    method: member.method || "",
    notes: member.notes || "",
    reminderSent: Boolean(member.reminderSent)
  };
}

function createMonth(label, sourceMembers = starterMembers) {
  return {
    id: makeId(),
    label,
    members: sourceMembers.map((member) => createMember(member))
  };
}

function normalizeState(parsed) {
  if (Array.isArray(parsed.months)) {
    parsed.months.forEach((month) => {
      if (Array.isArray(month.members)) {
        month.members.forEach((member) => {
          const digits = last10Digits(member.phone);
          if (digits && member.name) {
            canonicalNamesByPhone[digits] = member.name;
          }
        });
      }
    });
  }

  const months = Array.isArray(parsed.months)
    ? parsed.months
        .filter((month) => Array.isArray(month.members))
        .map((month) => ({
          id: month.id || makeId(),
          label: month.label || monthLabel(),
          members: month.members.map(normalizeMember)
        }))
    : [];

  if (!months.length) {
    const firstMonth = createMonth(monthLabel());
    return { activeMonthId: firstMonth.id, months: [firstMonth] };
  }

  const activeMonthId = months.some((month) => month.id === parsed.activeMonthId)
    ? parsed.activeMonthId
    : months[0].id;
  return { activeMonthId, months };
}

function loadState() {
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored) {
    try {
      const parsed = JSON.parse(stored);
      if (parsed.months?.length) return normalizeState(parsed);
    } catch {}
  }
  const firstMonth = createMonth(monthLabel());
  return { activeMonthId: firstMonth.id, months: [firstMonth] };
}

function saveState() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  queueServerSave();
}

function setSyncStatus(text) {
  els.syncStatus.textContent = text;
}

async function syncFromServer() {
  try {
    const response = await fetch("./api/state", { cache: "no-store" });
    if (!response.ok) throw new Error("sync unavailable");
    const result = await response.json();
    if (result.ok && result.state?.months?.length) {
      state = normalizeState(result.state);
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
      render();
      setSyncStatus("Synced");
    } else {
      setSyncStatus("Saving...");
      await saveToServer();
    }
  } catch {
    setSyncStatus("Local only");
  }
}

function queueServerSave() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveToServer, 450);
}

async function saveToServer() {
  try {
    setSyncStatus("Saving...");
    const response = await fetch("./api/state", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(state)
    });
    if (!response.ok) throw new Error("save failed");
    const result = await response.json();
    setSyncStatus(result.ok ? "Synced" : "Local only");
  } catch {
    setSyncStatus("Local only");
  }
}

function activeMonth() {
  return state.months.find((month) => month.id === state.activeMonthId) || state.months[0];
}

function totals(month = activeMonth()) {
  const totalDue = month.members.reduce((sum, member) => sum + Number(member.due || 0), 0);
  const collected = month.members.reduce((sum, member) => sum + Number(member.received || 0), 0);
  const paid = month.members.filter((member) => member.paid).length;
  const pending = month.members.length - paid;
  return {
    totalDue,
    collected,
    remaining: Math.max(totalDue - collected, 0),
    paid,
    pending,
    percent: month.members.length ? Math.round((paid / month.members.length) * 100) : 0
  };
}

function render() {
  renderMonthSelect();
  renderSummary();
  renderWorkflow();
  renderMembers();
  renderHistory();
  renderPending();
  renderMessage();
}

function renderMonthSelect() {
  els.monthSelect.innerHTML = "";
  state.months.forEach((month) => {
    const option = document.createElement("option");
    option.value = month.id;
    option.textContent = month.label;
    els.monthSelect.appendChild(option);
  });
  els.monthSelect.value = activeMonth().id;
}

function renderSummary() {
  const month = activeMonth();
  const total = totals(month);
  els.remainingAmount.textContent = money(total.remaining);
  els.progressText.textContent = `${total.paid} of ${month.members.length} paid`;
  els.progressCircle.style.strokeDashoffset = String(ringLength - ringLength * (total.percent / 100));
  els.progressValue.textContent = `${total.percent}%`;
  els.totalBill.textContent = money(total.totalDue);
  els.totalCollected.textContent = money(total.collected);
  els.pendingCount.textContent = total.pending;
}

function renderWorkflow() {
  const month = activeMonth();
  const total = totals(month);
  const membersWithAmounts = month.members.filter((member) => Number(member.due || 0) > 0).length;
  els.workflowMonth.textContent = month.label;
  els.workflowMembers.textContent = `${membersWithAmounts}/${month.members.length}`;
  els.workflowPending.textContent = money(total.remaining);
  els.workflowStatus.textContent = total.pending === 0
    ? "Closed"
    : total.collected > 0
      ? "Collecting"
      : "Open";
}

function renderMembers() {
  const month = activeMonth();
  const visibleMembers = els.unpaidOnlyInput.checked
    ? month.members.filter((member) => !member.paid)
    : month.members;

  els.memberList.innerHTML = "";
  visibleMembers.forEach((member) => {
    const node = els.memberTemplate.content.firstElementChild.cloneNode(true);
    node.classList.toggle("is-paid", member.paid);
    node.querySelector("h3").textContent = member.name;
    node.querySelector("p").textContent = member.phone;
    node.querySelector(".amount-due").textContent = money(member.due);

    const checkButton = node.querySelector(".check-button");
    checkButton.classList.toggle("is-done", member.paid);
    checkButton.addEventListener("click", () => togglePaid(member.id));

    const dueInput = node.querySelector(".due-input");
    dueInput.value = Number(member.due || 0).toFixed(2);
    dueInput.addEventListener("change", () => updateMember(member.id, { due: Number(dueInput.value || 0) }));

    const receivedInput = node.querySelector(".received-input");
    receivedInput.value = Number(member.received || 0).toFixed(2);
    receivedInput.addEventListener("change", () => updateMember(member.id, { received: Number(receivedInput.value || 0) }));

    const dateInput = node.querySelector(".date-input");
    dateInput.value = member.datePaid;
    dateInput.addEventListener("change", () => updateMember(member.id, { datePaid: dateInput.value }));

    const methodInput = node.querySelector(".method-input");
    methodInput.value = member.method;
    methodInput.addEventListener("change", () => updateMember(member.id, { method: methodInput.value }));

    const notesInput = node.querySelector(".notes-input");
    notesInput.value = member.notes;
    notesInput.addEventListener("change", () => updateMember(member.id, { notes: notesInput.value }));

    const reminderInput = node.querySelector(".reminder-input");
    reminderInput.checked = member.reminderSent;
    reminderInput.addEventListener("change", () => updateMember(member.id, { reminderSent: reminderInput.checked }));

    node.querySelector(".edit-member-button").addEventListener("click", () => editMember(member.id));
    node.querySelector(".remove-member-button").addEventListener("click", () => removeMember(member.id));

    els.memberList.appendChild(node);
  });

  if (!visibleMembers.length) {
    els.memberList.innerHTML = '<article class="pending-row"><div><h3>All paid</h3><p>No unpaid members for this month.</p></div><strong>$0.00</strong></article>';
  }
}

function addMember() {
  const name = prompt("Member name");
  if (!name?.trim()) return;
  const phone = prompt("Phone number", "") || "";
  const due = Number(prompt("Default amount due", "0") || 0);
  const cleanedName = name.trim();
  const cleanedPhone = phone.trim();
  const digits = last10Digits(cleanedPhone);
  if (digits && cleanedName) {
    canonicalNamesByPhone[digits] = cleanedName;
  }
  const member = createMember({ name: cleanedName, phone: cleanedPhone, due: Math.max(due, 0) });
  activeMonth().members.push(member);
  saveState();
  render();
}

function editMember(id) {
  const member = activeMonth().members.find((entry) => entry.id === id);
  if (!member) return;
  const name = prompt("Member name", member.name);
  if (!name?.trim()) return;
  const phone = prompt("Phone number", member.phone) || "";
  const oldKey = memberKey(member);
  const newName = name.trim();
  const newPhone = phone.trim();

  const digits = last10Digits(newPhone);
  if (digits && newName) {
    canonicalNamesByPhone[digits] = newName;
  }

  state.months.forEach((month) => {
    month.members.forEach((entry) => {
      if (memberKey(entry) !== oldKey) return;
      entry.name = newName;
      entry.phone = newPhone;
    });
  });
  saveState();
  render();
}

function removeMember(id) {
  const member = activeMonth().members.find((entry) => entry.id === id);
  if (!member) return;
  if (!confirm(`Remove ${member.name} from ${activeMonth().label}?`)) return;
  activeMonth().members = activeMonth().members.filter((entry) => entry.id !== id);
  saveState();
  render();
}

function memberKey(member) {
  const digits = last10Digits(member.phone);
  return digits || normalize(member.name);
}

function allMemberOptions() {
  const options = new Map();
  state.months.forEach((month) => {
    month.members.forEach((member) => {
      const key = memberKey(member);
      if (!key || options.has(key)) return;
      options.set(key, {
        key,
        name: member.name,
        phone: member.phone
      });
    });
  });
  return [...options.values()].sort((a, b) => a.name.localeCompare(b.name));
}

function renderHistory() {
  const options = allMemberOptions();
  const previousValue = els.historyMemberSelect.value || localStorage.getItem("bill-collect-history-member") || "";
  els.historyMemberSelect.innerHTML = "";

  options.forEach((member) => {
    const option = document.createElement("option");
    option.value = member.key;
    option.textContent = member.phone ? `${member.name} (${member.phone})` : member.name;
    els.historyMemberSelect.appendChild(option);
  });

  const selectedKey = options.some((member) => member.key === previousValue)
    ? previousValue
    : options[0]?.key || "";
  els.historyMemberSelect.value = selectedKey;
  if (selectedKey) localStorage.setItem("bill-collect-history-member", selectedKey);

  const rows = selectedKey
    ? state.months
        .map((month) => {
          const member = month.members.find((entry) => memberKey(entry) === selectedKey);
          if (!member) return null;
          const due = Number(member.due || 0);
          const paid = Number(member.received || 0);
          return {
            month: month.label,
            due,
            paid,
            pending: Math.max(due - paid, 0),
            isPaid: member.paid,
            datePaid: member.datePaid,
            method: member.method,
            notes: member.notes
          };
        })
        .filter(Boolean)
    : [];

  const totalDue = rows.reduce((sum, row) => sum + row.due, 0);
  const totalPaid = rows.reduce((sum, row) => sum + row.paid, 0);
  const totalPending = Math.max(totalDue - totalPaid, 0);
  els.historyTotalDue.textContent = money(totalDue);
  els.historyTotalPaid.textContent = money(totalPaid);
  els.historyPending.textContent = money(totalPending);

  els.historyList.innerHTML = "";
  rows.forEach((row) => {
    const article = document.createElement("article");
    article.className = "history-row";
    article.innerHTML = `
      <header>
        <div>
          <h3>${escapeHtml(row.month)}</h3>
          <p>${row.datePaid ? `Paid ${escapeHtml(row.datePaid)}` : "No paid date"}${row.method ? ` · ${escapeHtml(row.method)}` : ""}</p>
        </div>
        <strong class="history-status ${row.pending <= 0 ? "is-paid" : ""}">${row.pending <= 0 ? "Clear" : "Pending"}</strong>
      </header>
      <div class="history-money">
        <div><span>Due</span><strong>${money(row.due)}</strong></div>
        <div><span>Paid</span><strong>${money(row.paid)}</strong></div>
        <div><span>Pending</span><strong>${money(row.pending)}</strong></div>
      </div>
      ${row.notes ? `<p>${escapeHtml(row.notes)}</p>` : ""}
    `;
    els.historyList.appendChild(article);
  });

  if (!rows.length) {
    els.historyList.innerHTML = '<article class="history-row"><header><div><h3>No history</h3><p>This member has no monthly records yet.</p></div><strong class="history-status is-paid">$0.00</strong></header></article>';
  }
}

function renderPending() {
  const pendingMembers = activeMonth().members.filter((member) => !member.paid);
  els.pendingList.innerHTML = "";
  pendingMembers.forEach((member) => {
    const row = document.createElement("article");
    row.className = "pending-row";
    row.innerHTML = `
      <div>
        <h3>${escapeHtml(member.name)}</h3>
        <p>${escapeHtml(member.phone)}${member.reminderSent ? " - reminder sent" : ""}</p>
      </div>
      <strong>${money(member.due)}</strong>
    `;
    els.pendingList.appendChild(row);
  });

  if (!pendingMembers.length) {
    els.pendingList.innerHTML = '<article class="pending-row"><div><h3>No pending payments</h3><p>Everyone is marked paid.</p></div><strong>$0.00</strong></article>';
  }
}

function renderMessage() {
  const month = activeMonth();
  const total = totals(month);
  const pendingMembers = month.members
    .map((member) => ({
      ...member,
      pendingAmount: Math.max(Number(member.due || 0) - Number(member.received || 0), 0)
    }))
    .filter((member) => member.pendingAmount > 0);
  const lines = [
    `Hi everyone, ${month.label} AT&T bill is ready.`,
    "",
    `Total bill: ${money(total.totalDue)} | Collected: ${money(total.collected)} | Remaining: ${money(total.remaining)}`,
    ""
  ];

  if (pendingMembers.length) {
    lines.push("Pending payments:");
    pendingMembers.forEach((member) => {
      lines.push(`- ${member.name}: ${money(member.pendingAmount)}`);
    });
    lines.push(
      "",
      "Please send your amount when you get a chance.",
      "After paying, reply Sent with the payment method, like: Sent Zelle."
    );
  } else {
    lines.push("Everyone is paid for this month. Thank you.");
  }

  els.messageBox.value = lines.join("\n");
}

function togglePaid(id) {
  const member = activeMonth().members.find((entry) => entry.id === id);
  if (!member) return;
  member.paid = !member.paid;
  if (member.paid) {
    member.received = Number(member.due || 0);
    member.datePaid ||= todayValue();
  } else {
    member.received = 0;
    member.datePaid = "";
  }
  saveState();
  render();
}

function updateMember(id, patch) {
  const member = activeMonth().members.find((entry) => entry.id === id);
  if (!member) return;
  Object.assign(member, patch);
  if (patch.due !== undefined) {
    member.due = Math.max(Number(member.due || 0), 0);
    if (member.paid) member.received = member.due;
  }
  if (patch.received !== undefined) {
    member.received = Math.max(Number(member.received || 0), 0);
    member.paid = member.received >= Number(member.due || 0) && Number(member.due || 0) > 0;
    if (member.paid) member.datePaid ||= todayValue();
  }
  saveState();
  render();
}

function createNextMonth() {
  openMonthDialog();
}

function openMonthDialog() {
  const current = activeMonth();
  const suggested = nextMonthParts(current.label);
  els.newMonthNameInput.value = suggested.month;
  els.newMonthYearInput.value = suggested.year;
  els.newMonthBillInput.value = "";
  els.newMonthStatus.textContent = "Choose the month. Adding a bill is optional.";
  if (typeof els.monthDialog.showModal === "function") {
    els.monthDialog.showModal();
  } else {
    els.monthDialog.setAttribute("open", "");
  }
}

function closeMonthDialog() {
  if (typeof els.monthDialog.close === "function") {
    els.monthDialog.close();
  } else {
    els.monthDialog.removeAttribute("open");
  }
}

async function submitNewMonth(event) {
  event.preventDefault();
  const label = `${els.newMonthNameInput.value} ${els.newMonthYearInput.value}`.trim();
  if (!label || !els.newMonthYearInput.value) return;
  let month = state.months.find((entry) => entry.label.toLowerCase() === label.toLowerCase());
  const current = activeMonth();
  const source = current.members.map((member) => ({
    name: member.name,
    phone: member.phone,
    due: member.due
  }));
  if (!month) {
    month = createMonth(label, source);
    state.months.push(month);
  }
  state.activeMonthId = month.id;
  saveState();
  render();
  closeMonthDialog();

  const file = els.newMonthBillInput.files?.[0];
  if (file) {
    selectView("import");
    els.importResults.textContent = `Created ${label}. Reading bill...`;
    const text = await extractFileText(file);
    if (text) {
      els.billTextInput.value = text;
      detectedBillMonth = detectBillMonth(text) || label;
      renderDetectedMonth();
      scanBillText();
    }
  }
}

function nextMonthName(label) {
  const parts = nextMonthParts(label);
  return `${parts.month} ${parts.year}`;
}

function nextMonthParts(label) {
  const match = label.match(/^([A-Za-z]+)\s+(\d{4})$/);
  const monthNames = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December"
  ];

  if (match) {
    const monthIndex = monthNames.findIndex((name) => name.toLowerCase() === match[1].toLowerCase());
    const year = Number(match[2]);
    if (monthIndex >= 0) {
      const nextIndex = (monthIndex + 1) % 12;
      const nextYear = nextIndex === 0 ? year + 1 : year;
      return { month: monthNames[nextIndex], year: nextYear };
    }
  }
  const now = new Date();
  return { month: monthNames[now.getMonth()], year: now.getFullYear() };
}

function renameMonth() {
  const month = activeMonth();
  const label = prompt("Month name", month.label);
  if (!label) return;
  month.label = label.trim();
  saveState();
  render();
}

async function copyMessage() {
  els.messageBox.focus();
  els.messageBox.select();
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(els.messageBox.value);
    } else {
      document.execCommand("copy");
    }
    els.copyStatus.textContent = "Copied. Paste it into WhatsApp.";
  } catch {
    els.copyStatus.textContent = "Select the text and copy it manually.";
  }
}

function markRemindersSent() {
  activeMonth().members.forEach((member) => {
    if (!member.paid) member.reminderSent = true;
  });
  saveState();
  render();
}

function normalize(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/[^a-z0-9]/g, "");
}

function phoneDigits(value) {
  return String(value || "").replace(/\D/g, "");
}

function last10Digits(value) {
  const digits = String(value || "").replace(/\D/g, "");
  return digits.length >= 10 ? digits.slice(-10) : digits;
}

function phonePattern(digits) {
  const last10 = digits.length >= 10 ? digits.slice(-10) : digits;
  return `${last10.slice(0, 3)}[.\\-\\s]?${last10.slice(3, 6)}[.\\-\\s]?${last10.slice(6)}`;
}

function findMemberByText(text) {
  const cleaned = normalize(text);
  const digits = last10Digits(text);
  return activeMonth().members.find((member) => {
    const memberDigits = last10Digits(member.phone);
    if (memberDigits && digits && memberDigits.includes(digits)) return true;
    if (memberDigits && digits && digits.includes(memberDigits)) return true;
    const name = normalize(member.name);
    if (name && cleaned.includes(name)) return true;
    const firstName = normalize(member.name.split(" ")[0]);
    return firstName.length > 3 && cleaned.includes(firstName);
  });
}

function scanBillText() {
  const text = els.billTextInput.value;
  if (!text.trim()) {
    els.importResults.textContent = "Add bill text or upload a PDF first.";
    return;
  }

  const lines = text.split(/\r?\n/);
  detectedBillMonth = detectBillMonth(text);
  renderDetectedMonth();
  billSuggestions = [];
  billAudit = null;
  const attTotals = extractAttBillTotals(text);

  activeMonth().members.forEach((member) => {
    const suggestion = suggestAmountForMember(member, lines, attTotals);
    if (!suggestion) return;
    billSuggestions.push(suggestion);
  });

  const matchedMemberIds = new Set(billSuggestions.map((suggestion) => suggestion.memberId));
  const missingMembers = activeMonth().members
    .filter((member) => !matchedMemberIds.has(member.id))
    .map((member) => member.name);
  billAudit = {
    billLineCount: attTotals.size,
    matchedCount: billSuggestions.length,
    missingMembers,
    parsedTotal: [...attTotals.values()].reduce((sum, entry) => sum + Number(entry.amount || 0), 0)
  };

  renderBillSuggestions();
  renderBillAudit();
  els.importResults.textContent = billSuggestions.length
    ? `Found ${billSuggestions.length} suggested amount${billSuggestions.length === 1 ? "" : "s"} from ${detectedBillMonth || "the bill"}. Review them, then tap Apply all.`
    : "No bill amounts matched. Use a text-based PDF export, or paste rows that include member name or phone number plus amount.";
}

function extractAttBillTotals(text) {
  const totals = new Map();
  const allText = String(text || "");

  const totalForRegex = /Total\s+for\s+(?:1[.\-\s]?)?(\d{3})[.\-\s]?(\d{3})[.\-\s]?(\d{4})\s+\$?\s*(\d{1,4}(?:,\d{3})*(?:\.\d{2}))/gi;
  let match;
  while ((match = totalForRegex.exec(allText))) {
    totals.set(`${match[1]}${match[2]}${match[3]}`, {
      amount: Number(match[4].replace(/,/g, "")),
      source: `Total for ${match[1]}.${match[2]}.${match[3]} ${money(Number(match[4].replace(/,/g, "")))}`,
      confidence: 99
    });
  }

  allText.split(/\r?\n/).forEach((line) => {
    const rowMatch = line.match(/^(?:1[.\-\s]?)?(\d{3})[.\-\s](\d{3})[.\-\s](\d{4})\s+.+?\$?\s*(-?\d{1,4}(?:,\d{3})*(?:\.\d{2}))\s*$/);
    if (!rowMatch) return;
    const digits = `${rowMatch[1]}${rowMatch[2]}${rowMatch[3]}`;
    if (totals.has(digits)) return;
    const amount = Number(rowMatch[4].replace(/,/g, ""));
    if (!Number.isFinite(amount) || amount <= 0 || amount >= 300) return;
    totals.set(digits, {
      amount,
      source: line.trim(),
      confidence: 96
    });
  });

  return totals;
}

function suggestAmountForMember(member, lines, attTotals = new Map()) {
  const memberName = normalize(member.name);
  const firstName = normalize(member.name.split(" ")[0]);
  const memberPhone = last10Digits(member.phone);
  const candidates = [];

  if (memberPhone && attTotals.has(memberPhone)) {
    const match = attTotals.get(memberPhone);
    return {
      memberId: member.id,
      name: member.name,
      oldAmount: Number(member.due || 0),
      newAmount: match.amount,
      confidence: match.confidence,
      source: match.source
    };
  }

  lines.forEach((line, index) => {
    const cleaned = normalize(line);
    const digits = phoneDigits(line);
    const matchesName = memberName && cleaned.includes(memberName);
    const matchesFirst = firstName.length > 3 && cleaned.includes(firstName);
    const matchesPhone = memberPhone && digits.includes(memberPhone);
    if (!matchesName && !matchesFirst && !matchesPhone) return;

    extractAmounts(line).forEach((amount) => {
      candidates.push({ amount, source: line.trim(), score: 4 });
    });

    const nearbyLines = lines.slice(Math.max(0, index - 1), Math.min(lines.length, index + 4));
    nearbyLines.forEach((nearbyLine, nearbyIndex) => {
      const distance = Math.abs(index - (Math.max(0, index - 1) + nearbyIndex));
      extractAmounts(nearbyLine).forEach((amount) => {
        candidates.push({
          amount,
          source: nearbyLine.trim() || line.trim(),
          score: Math.max(1, 3 - distance)
        });
      });
    });
  });

  const usable = candidates
    .filter((candidate) => Number.isFinite(candidate.amount) && candidate.amount >= 5 && candidate.amount < 250)
    .map((candidate) => ({
      ...candidate,
      score: candidate.score + amountSimilarityScore(candidate.amount, member.due)
    }))
    .sort((a, b) => b.score - a.score);
  if (!usable.length) return null;

  const best = usable[0];
  return {
    memberId: member.id,
    name: member.name,
    oldAmount: Number(member.due || 0),
    newAmount: best.amount,
    confidence: Math.min(99, Math.round(best.score * 14)),
    source: best.source
  };
}

function amountSimilarityScore(amount, oldAmount) {
  const previous = Number(oldAmount || 0);
  if (!previous) return 0;
  const difference = Math.abs(amount - previous);
  if (difference <= 2) return 4;
  if (difference <= 10) return 3;
  if (difference <= 25) return 1;
  return 0;
}

function extractAmounts(text) {
  const matches = String(text).match(/\$?\s*(\d{1,4}(?:,\d{3})*(?:\.\d{2}))/g) || [];
  return matches.map((match) => Number(match.replace(/[$,\s]/g, "")));
}

function renderBillSuggestions() {
  els.billSuggestionList.innerHTML = "";
  els.applyBillButton.disabled = billSuggestions.length === 0;

  billSuggestions.forEach((suggestion) => {
    const row = document.createElement("article");
    row.className = "suggestion-row";
    row.innerHTML = `
      <div>
        <h4>${escapeHtml(suggestion.name)}</h4>
        <p>${money(suggestion.oldAmount)} -> ${money(suggestion.newAmount)} · ${suggestion.confidence}% match</p>
        <small>${escapeHtml(suggestion.source || "Matched from bill text")}</small>
      </div>
      <div class="suggestion-actions">
        <strong>${money(suggestion.newAmount)}</strong>
        <button class="mini-button" type="button">Apply</button>
      </div>
    `;
    row.querySelector(".mini-button").addEventListener("click", () => applySingleSuggestion(suggestion.memberId));
    els.billSuggestionList.appendChild(row);
  });
}

function renderBillAudit() {
  if (!billAudit) {
    els.billAuditSummary.hidden = true;
    els.billAuditSummary.innerHTML = "";
    return;
  }

  const missing = billAudit.missingMembers.length
    ? `Needs review: ${billAudit.missingMembers.join(", ")}`
    : "Every current member matched to a bill line.";

  els.billAuditSummary.hidden = false;
  els.billAuditSummary.innerHTML = `
    <article><span>Bill lines</span><strong>${billAudit.billLineCount}</strong></article>
    <article><span>Matched</span><strong>${billAudit.matchedCount}/${activeMonth().members.length}</strong></article>
    <article><span>Parsed total</span><strong>${money(billAudit.parsedTotal)}</strong></article>
    <p>${escapeHtml(missing)}</p>
  `;
}

function detectBillMonth(text) {
  const monthNames = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December"
  ];
  const shortMonths = monthNames.map((month) => month.slice(0, 3));
  const monthPattern = [...monthNames, ...shortMonths].join("|");
  const autoPayMatch = text.match(new RegExp(`AutoPay[^\\n]*(?:scheduled|debit)[^\\n]*\\b(${monthPattern})\\.?\\s+\\d{1,2},?\\s+([12][0-9]{3})`, "i"))
    || text.match(new RegExp(`AutoPay[^\\n]*\\n\\s*(${monthPattern})\\.?\\s+\\d{1,2},?\\s+([12][0-9]{3})`, "i"));
  if (autoPayMatch) {
    return `${canonicalMonth(autoPayMatch[1], monthNames)} ${autoPayMatch[2]}`;
  }

  const namedMatch = text.match(new RegExp(`\\b(${monthPattern})\\s+([12][0-9]{3})\\b`, "i"));
  if (namedMatch) {
    return `${canonicalMonth(namedMatch[1], monthNames)} ${namedMatch[2]}`;
  }

  const dateMatch = text.match(/\b(\d{1,2})\/(\d{1,2})\/([12][0-9]{3}|\d{2})\b/);
  if (!dateMatch) return "";
  const monthIndex = Number(dateMatch[1]) - 1;
  if (monthIndex < 0 || monthIndex > 11) return "";
  const year = dateMatch[3].length === 2 ? `20${dateMatch[3]}` : dateMatch[3];
  return `${monthNames[monthIndex]} ${year}`;
}

function canonicalMonth(value, monthNames) {
  const lowered = value.toLowerCase().replace(".", "");
  const index = monthNames.findIndex((month) => month.toLowerCase() === lowered || month.slice(0, 3).toLowerCase() === lowered);
  return monthNames[Math.max(index, 0)];
}

function renderDetectedMonth() {
  if (!detectedBillMonth) {
    els.detectedMonthBox.hidden = true;
    return;
  }
  const existing = state.months.find((month) => month.label.toLowerCase() === detectedBillMonth.toLowerCase());
  const action = existing?.id === activeMonth().id
    ? "already selected"
    : existing
      ? "switch to it"
      : "create it";
  els.detectedMonthText.textContent = `Detected ${detectedBillMonth}. Tap Use month to ${action}.`;
  els.detectedMonthBox.hidden = false;
}

function useDetectedMonth() {
  if (!detectedBillMonth) return;
  let month = state.months.find((entry) => entry.label.toLowerCase() === detectedBillMonth.toLowerCase());
  if (!month) {
    const source = activeMonth().members.map((member) => ({
      name: member.name,
      phone: member.phone,
      due: member.due
    }));
    month = createMonth(detectedBillMonth, source);
    state.months.push(month);
  }
  state.activeMonthId = month.id;
  saveState();
  render();
  renderDetectedMonth();
  scanBillText();
}

function applyBillSuggestions() {
  let applied = 0;
  billSuggestions.forEach((suggestion) => {
    if (applySuggestion(suggestion)) applied += 1;
  });
  billSuggestions = [];
  saveState();
  render();
  renderBillSuggestions();
  renderBillAudit();
  els.importResults.textContent = `Applied ${applied} suggested amount${applied === 1 ? "" : "s"} to ${activeMonth().label}.`;
}

function applySingleSuggestion(memberId) {
  const suggestion = billSuggestions.find((entry) => entry.memberId === memberId);
  if (!suggestion || !applySuggestion(suggestion)) return;
  billSuggestions = billSuggestions.filter((entry) => entry.memberId !== memberId);
  saveState();
  render();
  renderBillSuggestions();
  renderBillAudit();
  els.importResults.textContent = `Applied ${suggestion.name}'s amount to ${activeMonth().label}.`;
}

function applySuggestion(suggestion) {
  const member = activeMonth().members.find((entry) => entry.id === suggestion.memberId);
  if (!member) return false;
  member.due = suggestion.newAmount;
  if (member.paid) member.received = suggestion.newAmount;
  return true;
}

function downloadFile(name, type, content) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function csvCell(value) {
  return `"${String(value ?? "").replace(/"/g, '""')}"`;
}

function exportCurrentMonthCsv() {
  const month = activeMonth();
  const rows = [
    ["Month", "Name", "Phone", "Amount Due", "Paid", "Amount Received", "Pending", "Date Paid", "Method", "Reminder Sent", "Notes"]
  ];
  month.members.forEach((member) => {
    const due = Number(member.due || 0);
    const received = Number(member.received || 0);
    rows.push([
      month.label,
      member.name,
      member.phone,
      due.toFixed(2),
      member.paid ? "Yes" : "No",
      received.toFixed(2),
      Math.max(due - received, 0).toFixed(2),
      member.datePaid,
      member.method,
      member.reminderSent ? "Yes" : "No",
      member.notes
    ]);
  });
  downloadFile(`${month.label.replace(/\s+/g, "-").toLowerCase()}-bill-collect.csv`, "text/csv", rows.map((row) => row.map(csvCell).join(",")).join("\n"));
}

function exportBackup() {
  downloadFile(`bill-collect-backup-${todayValue()}.json`, "application/json", JSON.stringify(state, null, 2));
}

function importBackup() {
  const file = els.importBackupInput.files?.[0];
  if (!file) return;
  const reader = new FileReader();
  reader.addEventListener("load", () => {
    try {
      const parsed = JSON.parse(String(reader.result || ""));
      state = normalizeState(parsed);
      saveState();
      render();
      els.importResults.textContent = "Backup restored successfully.";
    } catch {
      els.importResults.textContent = "Backup could not be restored. Choose a Bill Collect JSON backup.";
    }
  });
  reader.readAsText(file);
}

function scanChatText() {
  const text = els.chatTextInput.value;
  const lines = text.split(/\r?\n/);
  const paymentWords = /\b(sent|paid|done|zelle|venmo|transferred|transfer|apple cash|cashapp|cash app)\b/i;
  const matched = [];

  lines.forEach((line) => {
    if (!paymentWords.test(line)) return;
    const sender = extractSender(line);
    const member = findMemberByText(sender || line);
    if (!member || member.paid) return;

    member.paid = true;
    member.received = Number(member.due || 0);
    member.datePaid = extractMessageDate(line) || todayValue();
    member.method = detectMethod(line, member.method);
    member.notes = sender ? `Matched WhatsApp: ${sender}` : "Matched WhatsApp message";
    matched.push(`${member.name} on ${member.datePaid}`);
  });

  saveState();
  render();
  els.importResults.textContent = matched.length
    ? `Marked ${matched.length} member${matched.length === 1 ? "" : "s"} paid: ${matched.join(", ")}.`
    : "No payment messages matched. Try exporting WhatsApp chat without media and scanning the full text.";
}

function extractSender(line) {
  const iosMatch = line.match(/^\[?\d{1,2}\/\d{1,2}\/\d{2,4},?\s+[^-\]]+\]?\s+-\s+([^:]+):/);
  if (iosMatch) return iosMatch[1].trim();
  const simpleMatch = line.match(/^([^:]{2,40}):\s+/);
  return simpleMatch ? simpleMatch[1].trim() : "";
}

function extractMessageDate(line) {
  const match = line.match(/\[?(\d{1,2})\/(\d{1,2})\/(\d{2,4})/);
  if (!match) return "";
  const month = match[1].padStart(2, "0");
  const day = match[2].padStart(2, "0");
  const rawYear = match[3];
  const year = rawYear.length === 2 ? `20${rawYear}` : rawYear;
  return `${year}-${month}-${day}`;
}

function detectMethod(line, fallback = "") {
  if (/zelle/i.test(line)) return "Zelle";
  if (/venmo/i.test(line)) return "Venmo";
  if (/cash/i.test(line)) return "Cash";
  return fallback || "Other";
}

async function readTextFile(input, target) {
  const file = input.files?.[0];
  if (!file) return;
  const text = await extractFileText(file);
  if (text) target.value = text;
}

async function extractFileText(file) {
  if (file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf")) {
    els.importResults.textContent = "Extracting PDF text...";
    try {
      const response = await fetch("./api/extract_pdf", {
        method: "POST",
        headers: { "Content-Type": "application/pdf" },
        body: file
      });
      const result = await response.json();
      if (!response.ok || !result.ok) {
        throw new Error(result.error || "PDF extraction failed.");
      }
      detectedBillMonth = detectBillMonth(result.text);
      renderDetectedMonth();
      const pageText = result.pageCount ? ` from ${result.pageCount} page${result.pageCount === 1 ? "" : "s"}` : "";
      els.importResults.textContent = `PDF text extracted${pageText}. Tap Suggest amounts to review the new bill amounts.`;
      return result.text;
    } catch (error) {
      els.importResults.textContent = `PDF could not be read here: ${error.message}. Use the Bill Collect backend URL or paste the bill text.`;
      return "";
    }
  }

  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.addEventListener("load", () => resolve(String(reader.result || "")));
    reader.addEventListener("error", () => {
      els.importResults.textContent = "File could not be read.";
      resolve("");
    });
    reader.readAsText(file);
  });
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;"
  }[char]));
}

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    selectView(tab.dataset.view);
  });
});

function selectView(viewName) {
  document.querySelectorAll(".tab").forEach((item) => item.classList.toggle("is-active", item.dataset.view === viewName));
  document.querySelectorAll(".view").forEach((view) => view.classList.remove("is-active"));
  document.querySelector(`#view-${viewName}`).classList.add("is-active");
}

els.monthSelect.addEventListener("change", () => {
  state.activeMonthId = els.monthSelect.value;
  saveState();
  render();
});
els.historyMemberSelect.addEventListener("change", () => {
  localStorage.setItem("bill-collect-history-member", els.historyMemberSelect.value);
  renderHistory();
});
els.monthForm.addEventListener("submit", submitNewMonth);
els.closeMonthDialogButton.addEventListener("click", closeMonthDialog);
els.cancelMonthButton.addEventListener("click", closeMonthDialog);
els.monthDialog.addEventListener("click", (event) => {
  if (event.target === els.monthDialog) closeMonthDialog();
});
els.addMemberButton.addEventListener("click", addMember);
els.exportMonthButton.addEventListener("click", exportCurrentMonthCsv);
els.newMonthButton.addEventListener("click", createNextMonth);
els.renameMonthButton.addEventListener("click", renameMonth);
els.unpaidOnlyInput.addEventListener("change", renderMembers);
els.markRemindersButton.addEventListener("click", markRemindersSent);
els.copyMessageButton.addEventListener("click", copyMessage);
els.billFileInput.addEventListener("change", () => readTextFile(els.billFileInput, els.billTextInput));
els.chatFileInput.addEventListener("change", () => readTextFile(els.chatFileInput, els.chatTextInput));
els.exportBackupButton.addEventListener("click", exportBackup);
els.importBackupInput.addEventListener("change", importBackup);
els.scanBillButton.addEventListener("click", scanBillText);
els.applyBillButton.addEventListener("click", applyBillSuggestions);
els.useDetectedMonthButton.addEventListener("click", useDetectedMonth);
els.scanChatButton.addEventListener("click", scanChatText);

if ("serviceWorker" in navigator && window.isSecureContext) {
  navigator.serviceWorker.register("./service-worker.js").catch(() => {});
}

render();
syncFromServer();
