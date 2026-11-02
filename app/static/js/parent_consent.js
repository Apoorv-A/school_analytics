/** Parent consent page: grant/withdraw via /api/consent/records (CSP-safe external script). */
(function () {
  const root = document.getElementById("parent-consent-root");
  if (!root) return;

  const noticeVersion = root.dataset.privacyNoticeVersion || "";

  function reload() {
    window.location.reload();
  }

  function record(studentId, purpose, status) {
    fetch("/api/consent/records", {
      method: "POST",
      credentials: "same-origin",
      headers: csrfHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        student_id: Number(studentId),
        purpose: purpose,
        status: status,
        privacy_notice_version: noticeVersion,
      }),
    })
      .then(function (r) {
        if (!r.ok) throw new Error("Request failed");
        reload();
      })
      .catch(function () {
        alert("Could not save your choice. Please try again.");
      });
  }

  root.addEventListener("click", function (event) {
    const button = event.target.closest("[data-consent-action]");
    if (!button || !root.contains(button)) return;
    event.preventDefault();
    const studentId = button.dataset.studentId;
    const purpose = button.dataset.purpose;
    const status = button.dataset.consentAction;
    if (!studentId || !purpose || !status) return;
    record(studentId, purpose, status);
  });
})();
