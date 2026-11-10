/**
 * F07: parent unread published-remark badge + drawer (in-app only).
 */
(function () {
  "use strict";

  var dialog = document.getElementById("parent-remark-notices");
  var openBtn = document.querySelector("[data-remark-notices-open]");
  if (!dialog || !openBtn || typeof dialog.show !== "function") return;

  var badge = document.querySelector("[data-remark-notices-badge]");
  var sr = document.querySelector("[data-remark-notices-sr]");
  var listEl = dialog.querySelector("[data-remark-notices-list]");
  var emptyEl = dialog.querySelector("[data-remark-notices-empty]");
  var closeBtn = dialog.querySelector("[data-remark-notices-close]");
  var scrimBtn = dialog.querySelector("[data-remark-notices-scrim]");
  var markAllBtn = dialog.querySelector("[data-remark-notices-mark-all]");
  var navRemarks = document.querySelector('.nav-link[href="/parent/remarks"]');

  function headers(json) {
    return typeof csrfHeaders === "function"
      ? csrfHeaders(json ? { "Content-Type": "application/json" } : {})
      : json
        ? { "Content-Type": "application/json" }
        : {};
  }

  function setCount(count) {
    var n = Number(count) || 0;
    if (badge) {
      if (n > 0) {
        badge.hidden = false;
        badge.textContent = n > 99 ? "99+" : String(n);
      } else {
        badge.hidden = true;
        badge.textContent = "";
      }
    }
    if (sr) {
      sr.textContent =
        n > 0 ? n + " unread teacher remark" + (n === 1 ? "" : "s") : "";
    }
    if (navRemarks) {
      var navBadge = navRemarks.querySelector(".nav-link__badge");
      if (n > 0) {
        if (!navBadge) {
          navBadge = document.createElement("span");
          navBadge.className = "nav-link__badge";
          navBadge.setAttribute("aria-hidden", "true");
          navRemarks.appendChild(navBadge);
        }
        navBadge.textContent = n > 99 ? "99+" : String(n);
      } else if (navBadge) {
        navBadge.remove();
      }
    }
    if (markAllBtn) {
      markAllBtn.disabled = n === 0;
    }
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function renderItems(items) {
    if (!listEl) return;
    Array.from(listEl.querySelectorAll(".remark-notices-item")).forEach(function (node) {
      node.remove();
    });
    if (!items || !items.length) {
      if (emptyEl) emptyEl.hidden = false;
      return;
    }
    if (emptyEl) emptyEl.hidden = true;
    items.forEach(function (item) {
      var card = el("article", "remark-notices-item");
      card.setAttribute("role", "listitem");
      var meta = el(
        "p",
        "remark-notices-item__meta",
        (item.student_name || "Student") +
          " \u00b7 " +
          (item.category_label || item.category || "") +
          " \u00b7 " +
          (item.term || "")
      );
      var body = el("p", "remark-notices-item__body", item.body_preview || "");
      var actions = el("div", "remark-notices-item__actions");
      var view = el("a", "btn btn--small", "View remarks");
      var viewParams = new URLSearchParams();
      viewParams.set("student_id", String(item.student_id));
      if (item.term_id) {
        viewParams.set("term_id", String(item.term_id));
      }
      view.href = "/parent/remarks?" + viewParams.toString();
      var mark = el("button", "btn btn--small", "Mark read");
      mark.type = "button";
      mark.setAttribute("data-remark-id", String(item.id));
      actions.appendChild(mark);
      actions.appendChild(view);
      card.appendChild(meta);
      card.appendChild(body);
      card.appendChild(actions);
      listEl.appendChild(card);
    });
  }

  function refreshRemarksTimeline() {
    if (!window.location.pathname.startsWith("/parent/remarks")) return;
    if (window.SchoolCharts && typeof window.SchoolCharts.refreshAll === "function") {
      window.SchoolCharts.refreshAll();
    }
  }

  function refreshCount() {
    return fetch("/api/parent/remark-notices/count", {
      credentials: "same-origin",
      headers: headers(false),
    })
      .then(function (res) {
        if (!res.ok) return null;
        return res.json();
      })
      .then(function (data) {
        if (data && typeof data.count === "number") {
          var hadBadge = badge && !badge.hidden;
          setCount(data.count);
          if (data.count > 0 && !hadBadge) refreshRemarksTimeline();
        }
      })
      .catch(function () { /* non-blocking */ });
  }

  function loadDrawer() {
    return fetch("/api/parent/remark-notices", {
      credentials: "same-origin",
      headers: headers(false),
    })
      .then(function (res) {
        if (!res.ok) return null;
        return res.json();
      })
      .then(function (data) {
        if (!data) return;
        renderItems(data.items || []);
        if (typeof data.count === "number") setCount(data.count);
      });
  }

  function markRead(payload) {
    return fetch("/api/parent/remark-notices/mark-read", {
      method: "POST",
      credentials: "same-origin",
      headers: headers(true),
      body: JSON.stringify(payload),
    })
      .then(function (res) {
        if (!res.ok) return null;
        return res.json();
      })
      .then(function (data) {
        if (data && typeof data.count === "number") setCount(data.count);
        return loadDrawer();
      });
  }

  function closeDrawer() {
    if (dialog.open) dialog.close();
  }

  function openDrawer() {
    loadDrawer()
      .then(function () {
        refreshRemarksTimeline();
      })
      .finally(function () {
        dialog.show();
        if (closeBtn) closeBtn.focus();
      });
  }

  openBtn.addEventListener("click", openDrawer);

  if (closeBtn) {
    closeBtn.addEventListener("click", closeDrawer);
  }
  if (scrimBtn) {
    scrimBtn.addEventListener("click", closeDrawer);
  }
  dialog.addEventListener("cancel", function (event) {
    event.preventDefault();
    closeDrawer();
  });

  dialog.addEventListener("click", function (event) {
    var link = event.target.closest("a[href^='/parent/remarks']");
    if (link) closeDrawer();
  });

  if (markAllBtn) {
    markAllBtn.addEventListener("click", function () {
      markRead({ mark_all: true });
    });
  }

  if (listEl) {
    listEl.addEventListener("click", function (event) {
      var btn = event.target.closest("[data-remark-id]");
      if (!btn) return;
      var id = Number(btn.getAttribute("data-remark-id"));
      if (!id) return;
      markRead({ remark_ids: [id] });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", refreshCount);
  } else {
    refreshCount();
  }
})();
