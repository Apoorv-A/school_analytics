/**
 * Accessible chart information panel (F01-04 / F02).
 * Fetches /api/charts/<key>/info and renders plain text via textContent only.
 */
(function () {
  "use strict";

  let dialog = null;
  let lastFocus = null;
  let titleEl = null;
  let bodyEl = null;
  let footerEl = null;

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function ensureDialog() {
    if (dialog) return dialog;

    dialog = document.createElement("dialog");
    dialog.className = "chart-info-dialog no-print";
    dialog.setAttribute("aria-labelledby", "chart-info-title");
    dialog.setAttribute("aria-describedby", "chart-info-body");

    const panel = el("div", "chart-info-dialog__panel");

    const head = el("header", "chart-info-dialog__head");
    titleEl = el("h2", "chart-info-dialog__title", "About this chart");
    titleEl.id = "chart-info-title";
    const closeBtn = el("button", "btn btn--sm btn--ghost", "Close");
    closeBtn.type = "button";
    closeBtn.setAttribute("data-chart-info-close", "");
    head.appendChild(titleEl);
    head.appendChild(closeBtn);

    bodyEl = el("div", "chart-info-dialog__body");
    bodyEl.id = "chart-info-body";
    footerEl = el("footer", "chart-info-dialog__foot");

    panel.appendChild(head);
    panel.appendChild(bodyEl);
    panel.appendChild(footerEl);
    dialog.appendChild(panel);
    document.body.appendChild(dialog);

    closeBtn.addEventListener("click", closeDialog);
    dialog.addEventListener("cancel", function (event) {
      event.preventDefault();
      closeDialog();
    });
    dialog.addEventListener("click", function (event) {
      if (event.target === dialog) closeDialog();
    });

    return dialog;
  }

  function closeDialog() {
    if (!dialog || !dialog.open) return;
    dialog.close();
    clear(bodyEl);
    clear(footerEl);
    if (lastFocus) {
      lastFocus.setAttribute("aria-expanded", "false");
      if (typeof lastFocus.focus === "function") {
        lastFocus.focus();
      }
    }
  }

  function appendGuideSection(container, guide) {
    const article = el("article", "chart-info-guide");
    article.appendChild(el("h3", null, "What this chart shows"));
    article.appendChild(el("p", null, guide.purpose || ""));

    const dl = el("dl", "chart-info-metric__details");
    function row(label, value) {
      if (!value) return;
      const wrap = el("div");
      wrap.appendChild(el("dt", null, label));
      wrap.appendChild(el("dd", null, value));
      dl.appendChild(wrap);
    }

    row("Horizontal axis", guide.xAxis);
    row("Vertical axis", guide.yAxis);
    row("How values are built", guide.aggregation);
    row("Filters on this chart", guide.filterBehavior);
    row("Sample size", guide.caveats);
    row("Limitations", guide.limitations);

    article.appendChild(dl);
    container.appendChild(article);
  }

  function appendMetricSection(container, metric) {
    const article = el("article", "chart-info-metric");
    article.appendChild(el("h3", null, metric.label || metric.id));
    article.appendChild(el("p", null, metric.summary || ""));

    const dl = el("dl", "chart-info-metric__details");
    function row(label, value) {
      if (!value) return;
      const wrap = el("div");
      wrap.appendChild(el("dt", null, label));
      wrap.appendChild(el("dd", null, value));
      dl.appendChild(wrap);
    }

    row("How it is calculated", metric.computation);
    row("Included", metric.includes);
    row("Excluded", metric.excludes);
    row("Filters", metric.filterSemantics);
    row("Reporting period", metric.reportingPeriod);
    row("Data source", metric.dataSource);
    row("Limitations", metric.limitations);
    row("Privacy", metric.privacyNote);

    article.appendChild(dl);
    container.appendChild(article);
  }

  function renderPayload(payload) {
    clear(bodyEl);
    clear(footerEl);

    titleEl.textContent = "About: " + (payload.title || payload.key);

    if (payload.chartGuide) {
      appendGuideSection(bodyEl, payload.chartGuide);
    }

    if (!payload.metrics || !payload.metrics.length) {
      bodyEl.appendChild(
        el(
          "p",
          null,
          "No metric definitions are registered for this view yet. " +
            "See the full metrics guide for related KPIs."
        )
      );
    } else {
      payload.metrics.forEach(function (metric) {
        appendMetricSection(bodyEl, metric);
      });
    }

    const link = document.createElement("a");
    link.href = payload.appendixHref || "/help/metrics";
    link.className = "btn btn--sm";
    link.textContent = "Open metrics guide";
    footerEl.appendChild(link);
  }

  function showError(message) {
    clear(bodyEl);
    clear(footerEl);
    bodyEl.appendChild(el("p", "state state--error", message));
  }

  async function openChartInfo(chartKey, trigger) {
    lastFocus = trigger;
    const dlg = ensureDialog();
    clear(bodyEl);
    clear(footerEl);
    bodyEl.appendChild(el("p", null, "Loading…"));
    dlg.showModal();
    trigger.setAttribute("aria-expanded", "true");

    try {
      const response = await fetch(
        "/api/charts/" + encodeURIComponent(chartKey) + "/info",
        {
          credentials: "same-origin",
          headers: { Accept: "application/json" },
        }
      );
      if (response.status === 401) {
        window.location.href = "/login";
        return;
      }
      const payload = await response.json().catch(function () {
        return null;
      });
      if (!response.ok) {
        const detail =
          payload && payload.detail ? payload.detail : "Could not load chart help.";
        showError(detail);
        return;
      }
      renderPayload(payload);
    } catch (err) {
      showError("Network error while loading chart help.");
    }

    const closeBtn = dlg.querySelector("[data-chart-info-close]");
    if (closeBtn) closeBtn.focus();
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-action='chart-info']").forEach(function (btn) {
      btn.setAttribute("aria-haspopup", "dialog");
      btn.setAttribute("aria-expanded", "false");
      btn.addEventListener("click", function () {
        const card = btn.closest("[data-chart]");
        if (!card) return;
        openChartInfo(card.getAttribute("data-chart"), btn);
      });
    });
  });

  window.SchoolChartInfo = { close: closeDialog };
})();
