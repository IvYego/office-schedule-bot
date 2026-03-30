(function () {
  const tg = window.Telegram && window.Telegram.WebApp;
  if (tg) {
    tg.ready();
    tg.expand();
    try {
      tg.setHeaderColor("#0c0c0e");
      tg.setBackgroundColor("#0c0c0e");
    } catch (_) {}
  }

  const initData = tg && tg.initData ? tg.initData : "";

  const $ = (id) => document.getElementById(id);
  const gate = $("gate");
  const main = $("main");
  const gateMsg = $("gate-msg");
  const heroName = $("hero-name");
  const monthTitle = $("month-title");
  const weekdaysEl = $("weekdays");
  const grid = $("grid");
  const btnPrev = $("btn-prev");
  const btnNext = $("btn-next");
  const selectionCount = $("selection-count");
  const btnClear = $("btn-clear");
  const btnApplyHome = $("btn-apply-home");
  const btnRemoveHome = $("btn-remove-home");

  const labels = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
  weekdaysEl.innerHTML = labels.map((l) => `<span>${l}</span>`).join("");

  let year = new Date().getFullYear();
  let month = new Date().getMonth() + 1;
  let monthPayload = null;
  const selected = new Set();

  function headers() {
    return { "X-Telegram-Init-Data": initData };
  }

  async function api(path, opts) {
    const r = await fetch(path, {
      ...opts,
      headers: { ...headers(), ...(opts && opts.headers) },
    });
    const text = await r.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch (_) {}
    if (!r.ok) {
      const msg = (data && data.detail) || r.statusText || "Ошибка";
      throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
    }
    return data;
  }

  function setSelectionCount() {
    selectionCount.textContent = "Выбрано: " + selected.size;
  }

  function renderCells() {
    if (!monthPayload || !monthPayload.days) return;
    grid.innerHTML = "";
    const first = monthPayload.days[0];
    if (!first) return;
    const startPad = first.weekday;
    for (let i = 0; i < startPad; i++) {
      const ph = document.createElement("div");
      ph.className = "cell-pad";
      grid.appendChild(ph);
    }
    monthPayload.days.forEach((d) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "cell";
      btn.textContent = String(d.day);
      if (d.state === "off") {
        btn.classList.add("off");
        btn.disabled = true;
      } else {
        if (d.state === "home") btn.classList.add("home");
        else btn.classList.add("office");
        if (selected.has(d.iso)) btn.classList.add("selected");
        btn.addEventListener("click", () => {
          if (selected.has(d.iso)) selected.delete(d.iso);
          else selected.add(d.iso);
          setSelectionCount();
          renderCells();
        });
      }
      grid.appendChild(btn);
    });
  }

  async function loadMonth() {
    monthTitle.textContent = "…";
    const data = await api(`/api/month?year=${year}&month=${month}`);
    monthPayload = data;
    monthTitle.textContent = data.title;
    renderCells();
    setSelectionCount();
  }

  async function bootstrap() {
    if (!initData) {
      gate.classList.remove("hidden");
      gateMsg.textContent = "Откройте из Telegram";
      return;
    }
    let me;
    try {
      me = await api("/api/me");
    } catch (e) {
      gate.classList.remove("hidden");
      gateMsg.textContent = e.message || "Не удалось войти";
      return;
    }
    if (!me.profile_complete) {
      gate.classList.remove("hidden");
      gateMsg.textContent = "Сначала выберите имя в боте: /start";
      return;
    }
    main.classList.remove("hidden");
    heroName.textContent = me.display_name;
    await loadMonth();
  }

  btnPrev.addEventListener("click", async () => {
    const p = monthPayload && monthPayload.prev;
    if (!p) return;
    year = p.year;
    month = p.month;
    selected.clear();
    await loadMonth();
  });
  btnNext.addEventListener("click", async () => {
    const n = monthPayload && monthPayload.next;
    if (!n) return;
    year = n.year;
    month = n.month;
    selected.clear();
    await loadMonth();
  });
  btnClear.addEventListener("click", () => {
    selected.clear();
    setSelectionCount();
    renderCells();
  });
  btnApplyHome.addEventListener("click", async () => {
    if (!selected.size || !monthPayload) return;
    try {
      await api("/api/month/apply", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          year,
          month,
          apply_home: Array.from(selected),
          remove_home: [],
        }),
      });
      selected.clear();
      await loadMonth();
      if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
    } catch (e) {
      if (tg && tg.showAlert) tg.showAlert(e.message);
      else alert(e.message);
    }
  });
  btnRemoveHome.addEventListener("click", async () => {
    if (!selected.size || !monthPayload) return;
    try {
      await api("/api/month/apply", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          year,
          month,
          apply_home: [],
          remove_home: Array.from(selected),
        }),
      });
      selected.clear();
      await loadMonth();
      if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
    } catch (e) {
      if (tg && tg.showAlert) tg.showAlert(e.message);
      else alert(e.message);
    }
  });

  bootstrap().catch((e) => {
    gate.classList.remove("hidden");
    gateMsg.textContent = e.message || "Ошибка";
  });
})();
