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

  function controls() {
    if (!form) return [];
    return Array.prototype.slice.call(form.querySelectorAll("[data-filter]"));
  }

  function queryString() {
    const params = new URLSearchParams();
    controls().forEach(function (control) {
      const value = (control.value || "").trim();
      if (value) params.set(control.getAttribute("data-filter"), value);
    });
    return params.toString();
  }

  function labelFor(control) {
    const wrapper = control.closest(".filter");
    const label = wrapper ? wrapper.querySelector("label") : null;
    return label ? label.textContent : control.getAttribute("data-filter");
  }

  function displayValue(control) {
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

    const active = controls().filter(function (control) {
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
    controls().forEach(function (control) {
      control.value = "";
    });
    announceChange();
  }

  if (form) {
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      announceChange();
    });
    controls().forEach(function (control) {
      control.addEventListener("change", announceChange);
    });
    const resetButton = form.querySelector("[data-action='reset-filters']");
    if (resetButton) {
      resetButton.addEventListener("click", function (event) {
        event.preventDefault();
        reset();
      });
    }
    document.addEventListener("DOMContentLoaded", renderSummary);
  }

  window.SchoolFilters = {
    queryString: queryString,
    reset: reset,
  };
})();
