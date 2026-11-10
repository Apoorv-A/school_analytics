/**
 * F04: optional role-aware welcome dialog (skip, reduced motion, no login block).
 */
(function () {
  "use strict";

  var dialog = document.getElementById("portal-welcome");
  if (!dialog || typeof dialog.showModal !== "function") return;

  var skipBtn = dialog.querySelector("[data-welcome-skip]");
  var finishBtn = dialog.querySelector("[data-welcome-finish]");
  var dismissing = false;

  function dismiss() {
    if (dismissing) return;
    dismissing = true;
    var headers = typeof csrfHeaders === "function"
      ? csrfHeaders({ "Content-Type": "application/json" })
      : { "Content-Type": "application/json" };
    fetch("/api/welcome/dismiss", {
      method: "POST",
      credentials: "same-origin",
      headers: headers,
      body: "{}",
    })
      .catch(function () { /* non-blocking; cookie may still apply on retry */ })
      .finally(function () {
        if (dialog.open) dialog.close();
        dismissing = false;
      });
  }

  function openWelcome() {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      dialog.classList.add("welcome-dialog--reduced");
    }
    dialog.showModal();
    var focusTarget = finishBtn || skipBtn;
    if (focusTarget) focusTarget.focus();
  }

  if (skipBtn) skipBtn.addEventListener("click", dismiss);
  if (finishBtn) finishBtn.addEventListener("click", dismiss);
  dialog.addEventListener("cancel", function (event) {
    event.preventDefault();
    dismiss();
  });
  dialog.addEventListener("click", function (event) {
    if (event.target === dialog) dismiss();
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", openWelcome);
  } else {
    openWelcome();
  }
})();
