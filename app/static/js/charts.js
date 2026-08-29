/**
 * Chart engine.
 *
 * Every card in a template declares `data-chart="<registry key>"`. This module
 * fetches the matching payload from /api/charts/<key>, applies the current filter
 * bar as query parameters, and renders whichever visualization the payload asks for.
 *
 * All text coming from the API is written with textContent or through Chart.js data
 * values, never by assigning HTML, so a student name or teacher remark can never be
 * interpreted as markup.
 */
(function () {
  "use strict";

  const PALETTE = [
    "#4f46e5", "#0ea5e9", "#10b981", "#f59e0b", "#ef4444",
    "#8b5cf6", "#14b8a6", "#ec4899", "#84cc16", "#f97316",
  ];
  const GRID = "rgba(148, 163, 184, 0.18)";
  const TICK = "#64748b";

  const registry = new Map();

  /* ------------------------------------------------------------ helpers */

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function isNumber(value) {
    return typeof value === "number" && Number.isFinite(value);
  }

  function fmtNumber(value, digits) {
    if (!isNumber(value)) return "--";
    return value.toFixed(digits === undefined ? 1 : digits);
  }

  function fmtCell(value, key) {
    if (value === null || value === undefined || value === "") return "--";
    if (isNumber(value)) {
      const isPercentish =
        /average|percent|attendance|pass_rate|gap|avg|highest|lowest/.test(key);
      if (isPercentish) return value.toFixed(1) + "%";
      return Number.isInteger(value) ? String(value) : value.toFixed(1);
    }
    return String(value);
  }

  function showState(holder, message, isError) {
    clear(holder);
    const state = el("div", "state" + (isError ? " state--error" : ""));
    state.appendChild(el("div", null, message));
    holder.appendChild(state);
  }

  function showLoading(holder) {
    clear(holder);
    const state = el("div", "state");
    state.appendChild(el("div", "spinner"));
    state.appendChild(el("div", null, "Loading"));
    holder.appendChild(state);
  }

  function hasAnyValue(series) {
    return (series || []).some((s) =>
      (s.data || []).some((v) => (isNumber(v) ? true : v && isNumber(v.y)))
    );
  }

  /* --------------------------------------------------------- chart kinds */

  function baseOptions(payload, opts) {
    const options = opts || {};
    const percentAxis = !(payload.meta && payload.meta.unit && payload.meta.unit !== "%");
    return {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: {
          display: options.legend !== false,
          position: "bottom",
          labels: {
            usePointStyle: true,
            pointStyle: "circle",
            boxWidth: 8,
            padding: 14,
            color: TICK,
            font: { size: 11.5 },
          },
        },
        tooltip: {
          backgroundColor: "#0f172a",
          padding: 10,
          cornerRadius: 8,
          titleFont: { size: 12 },
          bodyFont: { size: 12 },
          callbacks: {
            label: function (ctx) {
              const raw = ctx.parsed.y === undefined ? ctx.parsed : ctx.parsed.y;
              if (!isNumber(raw)) return ctx.dataset.label + ": --";
              const suffix = percentAxis ? "%" : "";
              return ctx.dataset.label + ": " + raw.toFixed(1) + suffix;
            },
          },
        },
      },
      scales: options.scales || {
        x: {
          grid: { display: false },
          ticks: { color: TICK, font: { size: 11 }, maxRotation: 60, autoSkip: true },
        },
        y: {
          beginAtZero: true,
          suggestedMax: percentAxis ? 100 : undefined,
          grid: { color: GRID },
          border: { display: false },
          ticks: {
            color: TICK,
            font: { size: 11 },
            callback: function (value) {
              return percentAxis ? value + "%" : value;
            },
          },
        },
      },
    };
  }

  function buildLine(holder, payload) {
    const canvas = el("canvas");
    holder.appendChild(canvas);
    const datasets = payload.series.map(function (series, index) {
      const color = PALETTE[index % PALETTE.length];
      const dashed = series.kind === "dashed";
      return {
        label: series.label,
        data: series.data,
        borderColor: color,
        backgroundColor: color + "22",
        borderWidth: dashed ? 1.5 : 2.4,
        borderDash: dashed ? [5, 4] : undefined,
        pointRadius: dashed ? 0 : 3,
        pointHoverRadius: 5,
        tension: 0.35,
        spanGaps: true,
        fill: payload.series.length === 1 && !dashed,
      };
    });
    return new Chart(canvas, {
      type: "line",
      data: { labels: payload.labels, datasets: datasets },
      options: baseOptions(payload),
    });
  }

  function buildBar(holder, payload, stacked) {
    const canvas = el("canvas");
    holder.appendChild(canvas);
    const datasets = payload.series.map(function (series, index) {
      const color = PALETTE[index % PALETTE.length];
      if (series.kind === "line") {
        return {
          type: "line",
          label: series.label,
          data: series.data,
          borderColor: "#0f172a",
          borderWidth: 2,
          borderDash: [5, 4],
          pointRadius: 3,
          tension: 0.3,
          spanGaps: true,
        };
      }
      return {
        type: "bar",
        label: series.label,
        data: series.data,
        backgroundColor: color + (stacked ? "e6" : "cc"),
        borderColor: color,
        borderWidth: 1,
        borderRadius: 5,
        maxBarThickness: 46,
      };
    });
    const options = baseOptions(payload);
    if (stacked) {
      options.scales.x.stacked = true;
      options.scales.y.stacked = true;
    }
    return new Chart(canvas, {
      type: "bar",
      data: { labels: payload.labels, datasets: datasets },
      options: options,
    });
  }

  function buildDoughnut(holder, payload) {
    const canvas = el("canvas");
    holder.appendChild(canvas);
    const data = payload.series[0] ? payload.series[0].data : [];
    return new Chart(canvas, {
      type: "doughnut",
      data: {
        labels: payload.labels,
        datasets: [
          {
            data: data,
            backgroundColor: payload.labels.map(function (_l, i) {
              return PALETTE[i % PALETTE.length] + "dd";
            }),
            borderColor: "#fff",
            borderWidth: 2,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "58%",
        plugins: {
          legend: {
            position: "right",
            labels: {
              usePointStyle: true,
              pointStyle: "circle",
              boxWidth: 8,
              padding: 11,
              color: TICK,
              font: { size: 11.5 },
            },
          },
          tooltip: {
            backgroundColor: "#0f172a",
            padding: 10,
            cornerRadius: 8,
            callbacks: {
              label: function (ctx) {
                const total = ctx.dataset.data.reduce(function (a, b) {
                  return a + (isNumber(b) ? b : 0);
                }, 0);
                const share = total ? ((ctx.parsed / total) * 100).toFixed(0) : "0";
                return ctx.label + ": " + ctx.parsed + " (" + share + "%)";
              },
            },
          },
        },
      },
    });
  }

  function buildGauge(holder, payload) {
    const wrapper = el("div");
    wrapper.style.position = "relative";
    wrapper.style.height = "100%";
    const canvas = el("canvas");
    wrapper.appendChild(canvas);

    const value = payload.meta && isNumber(payload.meta.value) ? payload.meta.value : 0;
    const centre = el("div");
    centre.style.position = "absolute";
    centre.style.inset = "0";
    centre.style.display = "grid";
    centre.style.placeItems = "center";
    centre.style.pointerEvents = "none";
    const stack = el("div");
    stack.style.textAlign = "center";
    stack.style.marginTop = "18px";
    const big = el("div", null, value.toFixed(1) + "%");
    big.style.fontSize = "30px";
    big.style.fontWeight = "700";
    big.style.color = "#0f172a";
    stack.appendChild(big);
    stack.appendChild(
      el(
        "div",
        "muted",
        "at or above " + (payload.meta ? payload.meta.threshold : 33) + "%"
      )
    );
    centre.appendChild(stack);
    wrapper.appendChild(centre);
    holder.appendChild(wrapper);

    return new Chart(canvas, {
      type: "doughnut",
      data: {
        labels: payload.labels,
        datasets: [
          {
            data: payload.series[0].data,
            backgroundColor: ["#10b981dd", "#e2e8f0"],
            borderWidth: 0,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        circumference: 180,
        rotation: -90,
        cutout: "72%",
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: "#0f172a",
            callbacks: {
              label: function (ctx) {
                return ctx.label + ": " + ctx.parsed.toFixed(1) + "%";
              },
            },
          },
        },
      },
    });
  }

  function buildRadar(holder, payload) {
    const canvas = el("canvas");
    holder.appendChild(canvas);
    return new Chart(canvas, {
      type: "radar",
      data: {
        labels: payload.labels,
        datasets: payload.series.map(function (series, index) {
          const color = PALETTE[index % PALETTE.length];
          return {
            label: series.label,
            data: series.data,
            borderColor: color,
            backgroundColor: color + "2e",
            borderWidth: 2,
            pointRadius: 3,
            pointBackgroundColor: color,
          };
        }),
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            position: "bottom",
            labels: { usePointStyle: true, pointStyle: "circle", boxWidth: 8, color: TICK },
          },
          tooltip: {
            backgroundColor: "#0f172a",
            callbacks: {
              label: function (ctx) {
                return ctx.dataset.label + ": " + fmtNumber(ctx.parsed.r) + "%";
              },
            },
          },
        },
        scales: {
          r: {
            beginAtZero: true,
            max: 100,
            angleLines: { color: GRID },
            grid: { color: GRID },
            pointLabels: { color: TICK, font: { size: 11.5, weight: "600" } },
            ticks: { display: false, stepSize: 25 },
          },
        },
      },
    });
  }

  function buildScatter(holder, payload) {
    const canvas = el("canvas");
    holder.appendChild(canvas);
    const points = payload.series[0] ? payload.series[0].data : [];
    const colourFor = { high: "#ef4444", watch: "#f59e0b", none: "#10b981" };
    return new Chart(canvas, {
      type: "scatter",
      data: {
        datasets: [
          {
            label: payload.series[0] ? payload.series[0].label : "Students",
            data: points,
            pointRadius: 4,
            pointHoverRadius: 7,
            backgroundColor: points.map(function (point) {
              return (colourFor[point.risk] || "#4f46e5") + "cc";
            }),
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: "#0f172a",
            padding: 10,
            cornerRadius: 8,
            callbacks: {
              label: function (ctx) {
                const point = ctx.raw;
                return [
                  point.label,
                  point.section || "",
                  "Attendance " + fmtNumber(point.x) + "%",
                  "Average " + fmtNumber(point.y) + "%",
                ].filter(Boolean);
              },
            },
          },
        },
        scales: {
          x: {
            title: {
              display: true,
              text: (payload.meta && payload.meta.x_label) || "Attendance (%)",
              color: TICK,
              font: { size: 11.5 },
            },
            grid: { color: GRID },
            ticks: { color: TICK, font: { size: 11 } },
          },
          y: {
            title: {
              display: true,
              text: (payload.meta && payload.meta.y_label) || "Average (%)",
              color: TICK,
              font: { size: 11.5 },
            },
            grid: { color: GRID },
            ticks: { color: TICK, font: { size: 11 } },
          },
        },
      },
    });
  }

  function heatColour(value) {
    if (!isNumber(value)) return "#f1f5f9";
    const stops = [
      [0, 239, 68, 68],
      [40, 249, 115, 22],
      [55, 250, 204, 21],
      [70, 132, 204, 22],
      [85, 16, 185, 129],
      [100, 5, 150, 105],
    ];
    let lower = stops[0];
    let upper = stops[stops.length - 1];
    for (let i = 0; i < stops.length - 1; i += 1) {
      if (value >= stops[i][0] && value <= stops[i + 1][0]) {
        lower = stops[i];
        upper = stops[i + 1];
        break;
      }
    }
    const range = upper[0] - lower[0] || 1;
    const t = Math.min(1, Math.max(0, (value - lower[0]) / range));
    const mix = function (a, b) {
      return Math.round(a + (b - a) * t);
    };
    return (
      "rgba(" +
      mix(lower[1], upper[1]) + "," +
      mix(lower[2], upper[2]) + "," +
      mix(lower[3], upper[3]) + ",0.62)"
    );
  }

  function buildHeatmap(holder, payload) {
    holder.style.height = "auto";
    const wrapper = el("div", "heatmap");
    const table = el("table", "heat");

    const thead = el("thead");
    const headRow = el("tr");
    headRow.appendChild(el("th", null, ""));
    payload.columns.forEach(function (column) {
      headRow.appendChild(el("th", null, column.label));
    });
    thead.appendChild(headRow);
    table.appendChild(thead);

    const tbody = el("tbody");
    payload.rows.forEach(function (row, rowIndex) {
      const tr = el("tr");
      const th = el("th", null, row.label);
      th.title = row.label;
      tr.appendChild(th);
      (payload.values[rowIndex] || []).forEach(function (value, colIndex) {
        const td = el("td", null, isNumber(value) ? value.toFixed(0) : "--");
        td.style.background = heatColour(value);
        const column = payload.columns[colIndex];
        td.title = row.label + " - " + (column ? column.label : "") + ": " +
          (isNumber(value) ? value.toFixed(1) + "%" : "not assessed");
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrapper.appendChild(table);
    holder.appendChild(wrapper);

    const legend = el("div", "heat-legend");
    legend.appendChild(el("span", null, "0%"));
    legend.appendChild(el("span", "heat-legend__bar"));
    legend.appendChild(el("span", null, "100%"));
    holder.appendChild(legend);
    return null;
  }

  function sortRows(rows, key, direction) {
    const factor = direction === "descending" ? -1 : 1;
    return rows.slice().sort(function (a, b) {
      const av = a[key];
      const bv = b[key];
      const aMissing = av === null || av === undefined;
      const bMissing = bv === null || bv === undefined;
      if (aMissing && bMissing) return 0;
      if (aMissing) return 1;
      if (bMissing) return -1;
      if (isNumber(av) && isNumber(bv)) return (av - bv) * factor;
      return String(av).localeCompare(String(bv)) * factor;
    });
  }

  function buildTable(holder, payload) {
    holder.style.height = "auto";
    const state = { key: null, direction: "ascending" };

    const wrapper = el("div", "table-scroll");
    const table = el("table", "data");
    const thead = el("thead");
    const headRow = el("tr");
    const tbody = el("tbody");

    function paint(rows) {
      clear(tbody);
      if (!rows.length) {
        const tr = el("tr");
        const td = el("td", "muted", "Nothing to show for these filters.");
        td.colSpan = payload.columns.length;
        tr.appendChild(td);
        tbody.appendChild(tr);
        return;
      }
      rows.forEach(function (row) {
        const tr = el("tr");
        payload.columns.forEach(function (column) {
          const value = row[column.key];
          const td = el("td", null, fmtCell(value, column.key));
          if (column.align === "right") td.classList.add("num");
          if (column.align === "center") td.classList.add("center");
          if (column.key === "reasons" || column.key === "why") td.classList.add("wrap");
          if (column.key === "status" && row._tone) {
            clear(td);
            td.appendChild(el("span", "pill pill--" + row._tone, String(value)));
            td.classList.add("center");
          } else if (
            row._tone &&
            (column.key === "average" || column.key === "percentage")
          ) {
            td.classList.add("tone-" + row._tone);
          }
          tr.appendChild(td);
        });
        tbody.appendChild(tr);
      });
    }

    payload.columns.forEach(function (column) {
      const th = el("th", column.align === "right" ? "num" : null, column.label);
      th.setAttribute("scope", "col");
      th.addEventListener("click", function () {
        if (state.key === column.key) {
          state.direction =
            state.direction === "ascending" ? "descending" : "ascending";
        } else {
          state.key = column.key;
          state.direction = "descending";
        }
        Array.prototype.forEach.call(headRow.children, function (node) {
          node.removeAttribute("aria-sort");
        });
        th.setAttribute("aria-sort", state.direction);
        paint(sortRows(payload.rows, state.key, state.direction));
      });
      headRow.appendChild(th);
    });

    thead.appendChild(headRow);
    table.appendChild(thead);
    table.appendChild(tbody);
    wrapper.appendChild(table);
    holder.appendChild(wrapper);
    paint(payload.rows);
    return null;
  }

  function buildKpis(holder, payload) {
    holder.style.height = "auto";
    const grid = el("div", "kpi-grid");
    payload.cards.forEach(function (card) {
      const box = el("div", "kpi kpi--" + (card.tone || "neutral"));
      box.appendChild(el("div", "kpi__label", card.label));
      box.appendChild(el("div", "kpi__value", card.value));
      if (card.hint) box.appendChild(el("div", "kpi__hint", card.hint));
      grid.appendChild(box);
    });
    holder.appendChild(grid);

    const meta = payload.meta || {};
    if ((meta.strengths && meta.strengths.length) || meta.consistency) {
      const notes = el("div", "filters__summary");
      if (meta.strengths && meta.strengths.length) {
        notes.appendChild(el("span", null, "Strongest:"));
        meta.strengths.forEach(function (name) {
          notes.appendChild(el("span", "chip", name));
        });
      }
      if (meta.weaknesses && meta.weaknesses.length) {
        notes.appendChild(el("span", null, "Needs work:"));
        meta.weaknesses.forEach(function (name) {
          notes.appendChild(el("span", "chip", name));
        });
      }
      if (meta.consistency) {
        notes.appendChild(el("span", null, "Consistency: " + meta.consistency));
      }
      holder.appendChild(notes);
    }
    return null;
  }

  function buildTimeline(holder, payload) {
    holder.style.height = "auto";
    if (!payload.items || !payload.items.length) {
      showState(holder, "No remarks have been recorded for these filters.");
      return null;
    }
    const list = el("div", "timeline");
    payload.items.forEach(function (item) {
      const entry = el("div", "timeline__item");
      const meta = el("div", "timeline__meta");
      meta.appendChild(el("span", "chip", item.category));
      meta.appendChild(el("span", null, item.term));
      meta.appendChild(el("span", null, item.teacher));
      if (item.subject && item.subject !== "General") {
        meta.appendChild(el("span", null, item.subject));
      }
      entry.appendChild(meta);
      entry.appendChild(el("div", "timeline__body", item.body));
      list.appendChild(entry);
    });
    holder.appendChild(list);
    return null;
  }

  const BUILDERS = {
    line: buildLine,
    bar: function (h, p) { return buildBar(h, p, false); },
    "stacked-bar": function (h, p) { return buildBar(h, p, true); },
    doughnut: buildDoughnut,
    gauge: buildGauge,
    radar: buildRadar,
    scatter: buildScatter,
    heatmap: buildHeatmap,
    table: buildTable,
    kpi: buildKpis,
    timeline: buildTimeline,
  };

  /* --------------------------------------------------------------- CSV */

  function toCsv(payload) {
    const lines = [];
    const escape = function (value) {
      if (value === null || value === undefined) return "";
      let text = String(value);
      // A spreadsheet reads a leading =, +, -, @, tab or CR as the start of a
      // formula. Names and remarks are free text, so those are neutralised. Only
      // strings are guarded, so a negative number stays a number.
      if (typeof value === "string" && /^[=+\-@\t\r]/.test(text)) {
        text = "'" + text;
      }
      return /[",\n]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
    };

    if (payload.kind === "table") {
      lines.push(payload.columns.map(function (c) { return escape(c.label); }).join(","));
      payload.rows.forEach(function (row) {
        lines.push(
          payload.columns.map(function (c) { return escape(row[c.key]); }).join(",")
        );
      });
    } else if (payload.kind === "kpi") {
      lines.push("Metric,Value,Detail");
      payload.cards.forEach(function (card) {
        lines.push([card.label, card.value, card.hint].map(escape).join(","));
      });
    } else if (payload.kind === "heatmap") {
      lines.push(
        [""].concat(payload.columns.map(function (c) { return c.label; })).map(escape).join(",")
      );
      payload.rows.forEach(function (row, index) {
        lines.push(
          [row.label].concat(payload.values[index] || []).map(escape).join(",")
        );
      });
    } else if (payload.kind === "timeline") {
      lines.push("Category,Term,Teacher,Subject,Remark");
      (payload.items || []).forEach(function (item) {
        lines.push(
          [item.category, item.term, item.teacher, item.subject, item.body]
            .map(escape)
            .join(",")
        );
      });
    } else if (payload.kind === "scatter") {
      lines.push("Student,Class,Attendance,Average,Status");
      ((payload.series[0] || {}).data || []).forEach(function (point) {
        lines.push([point.label, point.section, point.x, point.y, point.risk].map(escape).join(","));
      });
    } else {
      lines.push(
        ["Label"].concat(payload.series.map(function (s) { return s.label; }))
          .map(escape)
          .join(",")
      );
      (payload.labels || []).forEach(function (label, index) {
        lines.push(
          [label]
            .concat(payload.series.map(function (s) { return s.data[index]; }))
            .map(escape)
            .join(",")
        );
      });
    }
    return lines.join("\n");
  }

  function downloadCsv(payload, key) {
    const blob = new Blob([toCsv(payload)], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = key.replace(/\./g, "-") + ".csv";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  }

  /* ------------------------------------------------------------ loading */

  function currentQuery() {
    return window.SchoolFilters ? window.SchoolFilters.queryString() : "";
  }

  async function loadCard(card) {
    const key = card.getAttribute("data-chart");
    const holder = card.querySelector("[data-chart-body]");
    if (!holder) return;

    const entry = registry.get(card) || {};
    if (entry.chart) {
      entry.chart.destroy();
      entry.chart = null;
    }
    showLoading(holder);

    const query = currentQuery();
    const url = "/api/charts/" + encodeURIComponent(key) + (query ? "?" + query : "");

    try {
      const response = await fetch(url, {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      if (response.status === 401) {
        window.location.href = "/login";
        return;
      }
      const payload = await response.json().catch(function () {
        return null;
      });
      if (!response.ok) {
        const detail =
          payload && payload.detail ? payload.detail : "Could not load this view.";
        showState(holder, detail, true);
        return;
      }

      registry.set(card, { payload: payload, chart: null });
      clear(holder);

      const builder = BUILDERS[payload.kind];
      if (!builder) {
        showState(holder, "Unsupported visualization: " + payload.kind, true);
        return;
      }

      const isEmpty =
        (payload.kind === "table" && !payload.rows.length) ||
        (payload.kind === "timeline" && !(payload.items || []).length) ||
        (payload.series && !hasAnyValue(payload.series) && payload.kind !== "kpi");
      if (isEmpty && payload.kind !== "kpi") {
        showState(holder, "No results match the current filters.");
        registry.set(card, { payload: payload, chart: null });
        return;
      }

      const chart = builder(holder, payload);
      registry.set(card, { payload: payload, chart: chart });

      const subtitle = card.querySelector("[data-chart-subtitle]");
      if (subtitle && payload.meta && payload.meta.hint) {
        subtitle.textContent = payload.meta.hint;
      }
    } catch (error) {
      showState(holder, "Network error while loading this view.", true);
    }
  }

  function refreshAll() {
    document.querySelectorAll("[data-chart]").forEach(loadCard);
  }

  function wireCardActions() {
    document.querySelectorAll("[data-chart]").forEach(function (card) {
      const exportButton = card.querySelector("[data-action='export']");
      if (exportButton) {
        exportButton.addEventListener("click", function () {
          const entry = registry.get(card);
          if (!entry || !entry.payload) return;
          downloadCsv(entry.payload, card.getAttribute("data-chart"));
        });
      }
      const reloadButton = card.querySelector("[data-action='reload']");
      if (reloadButton) {
        reloadButton.addEventListener("click", function () {
          loadCard(card);
        });
      }
    });

    document.querySelectorAll("[data-action='print']").forEach(function (button) {
      button.addEventListener("click", function () {
        window.print();
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    wireCardActions();
    refreshAll();
    document.addEventListener("filters:changed", refreshAll);
  });

  window.SchoolCharts = { refreshAll: refreshAll };
})();
