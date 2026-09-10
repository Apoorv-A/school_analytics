/**
 * Filter bar.
 *
 * Owns the current filter state, keeps it in the address bar so a filtered view can
 * be bookmarked or shared, and tells the chart engine when to reload. Options are
 * rendered server-side already narrowed to what the signed-in user may select.
 */
(function () {
  "use strict";

  const form = document.querySelector("[data-filters]");

  /** Repeated subject_ids / section_ids from the URL, kept outside the form controls. */
  const PASSTHROUGH_KEYS = ["subject_ids", "section_ids"];
  const passthrough = { subject_ids: [], section_ids: [] };

  function controls() {
    if (!form) return [];
    return Array.prototype.slice.call(form.querySelectorAll("[data-filter]"));
  }

  /**
   * Controls the user can actually change. Pinned values belong to the route that
   * rendered the page, so they are sent with every request but are not the user's
   * choice: clearing or listing them as active filters would be wrong.
   */
  function editableControls() {
    return controls().filter(function (control) {
      return !control.hasAttribute("data-filter-pinned");
    });
  }

  function passthroughControl(key) {
    if (!form) return null;
    if (key === "subject_ids") return form.querySelector('[data-filter="subject_id"]');
    if (key === "section_ids") return form.querySelector('[data-filter="section_id"]');
    return null;
  }

  function dropdownValue(key) {
    const control = passthroughControl(key);
    return control ? (control.value || "").trim() : "";
  }

  function initPassthrough() {
    PASSTHROUGH_KEYS.forEach(function (key) {
      passthrough[key] = [];
    });
    if (!form) return;

    const urlParams = new URLSearchParams(window.location.search);
    PASSTHROUGH_KEYS.forEach(function (key) {
      const fromUrl = urlParams.getAll(key).map(function (value) {
        return value.trim();
      }).filter(Boolean);
      if (fromUrl.length) {
        passthrough[key] = fromUrl;
        return;
      }
      const fromDom = Array.prototype.map.call(
        form.querySelectorAll('[data-passthrough="' + key + '"]'),
        function (input) {
          return (input.value || "").trim();
        }
      ).filter(Boolean);
      if (fromDom.length) passthrough[key] = fromDom;
    });
  }

  function clearPassthrough() {
    PASSTHROUGH_KEYS.forEach(function (key) {
      passthrough[key] = [];
    });
    if (!form) return;
    form.querySelectorAll("[data-passthrough]").forEach(function (input) {
      input.parentNode.removeChild(input);
    });
  }

  /**
   * Build the chart/query string from editable controls plus any pinned multi-id
   * params.
   *
   * Precedence per dimension (matches server FilterParams.resolved_*_ids):
   * repeated subject_ids / section_ids from the URL win when present; otherwise
   * the singular subject_id / section_id dropdown is used. Bookmarks like
   * ?subject_ids=1&subject_ids=2 therefore survive even if the page also
   * pre-selects one id in the dropdown.
   */
  function queryString() {
    const params = new URLSearchParams();
    controls().forEach(function (control) {
      const key = control.getAttribute("data-filter");
      const value = (control.value || "").trim();
      if (value) params.set(key, value);
    });

    if (passthrough.subject_ids.length) {
      params.delete("subject_id");
      params.delete("subject_ids");
      passthrough.subject_ids.forEach(function (id) {
        params.append("subject_ids", id);
      });
    } else if (dropdownValue("subject_ids")) {
      params.delete("subject_ids");
    } else {
      params.delete("subject_id");
    }

    if (passthrough.section_ids.length) {
      params.delete("section_id");
      params.delete("section_ids");
      passthrough.section_ids.forEach(function (id) {
        params.append("section_ids", id);
      });
    } else if (dropdownValue("section_ids")) {
      params.delete("section_ids");
    } else {
      params.delete("section_id");
    }

    return params.toString();
  }

  function labelFor(control) {
    const wrapper = control.closest(".filter");
    const label = wrapper ? wrapper.querySelector("label") : null;
    return label ? label.textContent : control.getAttribute("data-filter");
  }

  function displayValue(control) {
    if (control.id === "f-student-id") {
      const searchInput = form ? form.querySelector("#f-student-search") : null;
      if (searchInput && (searchInput.value || "").trim()) {
        return searchInput.value;
      }
    }
    if (control.tagName === "SELECT") {
      const option = control.options[control.selectedIndex];
      return option ? option.textContent : control.value;
    }
    return control.value;
  }

  function renderSummary() {
    const summary = document.querySelector("[data-filter-summary]");
    if (!summary) return;
    while (summary.firstChild) summary.removeChild(summary.firstChild);

    const active = editableControls().filter(function (control) {
      return (control.value || "").trim() !== "";
    });
    if (!active.length) {
      const span = document.createElement("span");
      span.textContent = "Showing everything available to you.";
      summary.appendChild(span);
      return;
    }
    const intro = document.createElement("span");
    intro.textContent = "Filtered by:";
    summary.appendChild(intro);
    active.forEach(function (control) {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.textContent = labelFor(control) + ": " + displayValue(control);
      summary.appendChild(chip);
    });
  }

  function syncUrl() {
    const query = queryString();
    const url = window.location.pathname + (query ? "?" + query : "");
    window.history.replaceState({}, "", url);
  }

  function announceChange() {
    syncUrl();
    renderSummary();
    document.dispatchEvent(new CustomEvent("filters:changed"));
  }

  function reset() {
    clearPassthrough();
    editableControls().forEach(function (control) {
      control.value = "";
    });
    const searchInput = form ? form.querySelector("#f-student-search") : null;
    if (searchInput) searchInput.value = "";
    const combobox = form ? form.querySelector("[data-student-combobox]") : null;
    if (combobox) {
      const results = combobox.querySelector("[data-student-results]");
      if (results) {
        while (results.firstChild) results.removeChild(results.firstChild);
        results.hidden = true;
      }
      combobox.classList.remove("is-open");
    }
    announceChange();
  }

  function initStudentCombobox(box) {
    const input = box.querySelector("#f-student-search");
    const hidden = box.querySelector("#f-student-id");
    const results = box.querySelector("[data-student-results]");
    if (!input || !hidden || !results) return;

    let timer = null;
    let open = false;
    let selectedLabel = hidden.value && input.value.trim() ? input.value.trim() : "";

    function isStudentDrillDownPage() {
      return /^\/admin\/student\/[^/]+(\/[^/]+)?$/.test(window.location.pathname);
    }

    function clearSelection() {
      const hadSelection = Boolean((hidden.value || "").trim() || selectedLabel);
      hidden.value = "";
      selectedLabel = "";
      if (hadSelection) announceChange();
    }

    function syncStudentFilterFromInput() {
      if (isStudentDrillDownPage()) return;
      const typed = input.value.trim();
      if (!typed) {
        if (hidden.value) clearSelection();
        else selectedLabel = "";
        return;
      }
      if (hidden.value && typed !== selectedLabel) clearSelection();
    }

    function clearResults() {
      while (results.firstChild) results.removeChild(results.firstChild);
      results.hidden = true;
      open = false;
      box.classList.remove("is-open");
      input.setAttribute("aria-expanded", "false");
    }

    function adminStudentTarget(studentId) {
      const path = window.location.pathname;
      if (path === "/admin/student") {
        return "/admin/student/" + studentId;
      }
      const match = path.match(/^\/admin\/student\/[^/]+(\/[^/]+)?$/);
      if (match) {
        return "/admin/student/" + studentId + (match[1] || "");
      }
      return null;
    }

    function selectStudent(row) {
      hidden.value = String(row.id);
      input.value = row.label;
      selectedLabel = row.label;
      clearResults();
      const target = adminStudentTarget(row.id);
      if (target) {
        const query = queryString();
        window.location.href = target + (query ? "?" + query : "");
        return;
      }
      announceChange();
    }

    function renderResults(rows) {
      while (results.firstChild) results.removeChild(results.firstChild);
      if (!rows.length) {
        const li = document.createElement("li");
        const empty = document.createElement("span");
        empty.className = "combobox__empty";
        empty.textContent = "No students found";
        li.appendChild(empty);
        results.appendChild(li);
      } else {
        rows.forEach(function (row) {
          const li = document.createElement("li");
          const btn = document.createElement("button");
          btn.type = "button";
          btn.className = "combobox__item";
          btn.setAttribute("role", "option");
          btn.textContent = row.label + " — " + (row.section || "");
          btn.addEventListener("mousedown", function (event) {
            event.preventDefault();
          });
          btn.addEventListener("click", function (event) {
            event.preventDefault();
            event.stopPropagation();
            selectStudent(row);
          });
          li.appendChild(btn);
          results.appendChild(li);
        });
      }
      results.hidden = false;
      open = true;
      input.setAttribute("aria-expanded", "true");
    }

    function fetchStudents(query) {
      return fetch(
        "/api/students/search?q=" + encodeURIComponent(query) + "&limit=50",
        { credentials: "same-origin" }
      )
        .then(function (response) { return response.json(); })
        .then(function (data) { return data.results || []; });
    }

    function showResults(query) {
      if (timer) clearTimeout(timer);
      box.classList.add("is-open");
      const delay = query.length >= 2 ? 250 : 0;
      timer = setTimeout(function () {
        fetchStudents(query).then(renderResults).catch(function () {
          renderResults([]);
        });
      }, delay);
    }

    input.setAttribute("role", "combobox");
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("aria-controls", results.id || "f-student-results");
    if (!results.id) results.id = "f-student-results";

    input.addEventListener("focus", function () {
      showResults(input.value.trim());
    });
    input.addEventListener("click", function () {
      showResults(input.value.trim());
    });
    input.addEventListener("input", function () {
      syncStudentFilterFromInput();
      showResults(input.value.trim());
    });
    input.addEventListener("keydown", function (event) {
      if (event.key === "Escape") clearResults();
    });

    results.addEventListener("mousedown", function (event) {
      event.preventDefault();
    });

    document.addEventListener("pointerdown", function (event) {
      if (!box.contains(event.target)) clearResults();
    });
    document.addEventListener("filters:changed", function () {
      if (!hidden.value) selectedLabel = "";
    });
  }

  if (form) {
    initPassthrough();
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      announceChange();
    });
    editableControls().forEach(function (control) {
      control.addEventListener("change", announceChange);
      if (control.type !== "date") {
        control.addEventListener("input", announceChange);
      }
    });
    const resetButton = form.querySelector("[data-action='reset-filters']");
    if (resetButton) {
      resetButton.addEventListener("click", function (event) {
        event.preventDefault();
        reset();
      });
    }
    const searchBox = form.querySelector("[data-student-combobox]");
    if (searchBox) initStudentCombobox(searchBox);
    renderSummary();
  }

  window.SchoolFilters = {
    queryString: queryString,
    reset: reset,
  };
})();
