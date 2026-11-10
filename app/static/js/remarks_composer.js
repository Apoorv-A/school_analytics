(function () {
  const root = document.getElementById("remark-composer");
  if (!root) return;

  const teacherName = root.dataset.teacherName || "Teacher";
  const categories = JSON.parse(root.dataset.categories || "[]");
  const categoryLabels = {};
  categories.forEach(function (pair) {
    categoryLabels[pair[0]] = pair[1];
  });

  const bodyEl = document.getElementById("remark-body");
  const categoryEl = document.getElementById("remark-category");
  const previewEl = document.getElementById("remark-family-preview");
  const previewNote = document.getElementById("remark-preview-note");
  const statusEl = document.getElementById("remark-status");
  const followUpEl = document.getElementById("remark-follow-up");
  const publishSavedBtn = document.getElementById("remark-publish-saved");
  const publishedWrap = document.getElementById("remark-published-context");
  const publishedList = document.getElementById("remark-published-list");
  const filterForm = document.querySelector("form[data-filters]");

  let savedDraftId = null;

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function filterValue(name) {
    if (!filterForm) return "";
    const control = filterForm.querySelector('[data-filter="' + name + '"]');
    return control && control.value ? control.value : "";
  }

  function selectedLabel(selectName) {
    if (!filterForm) return "";
    const control = filterForm.querySelector('[data-filter="' + selectName + '"]');
    if (!control || control.tagName !== "SELECT") return "";
    const opt = control.selectedOptions[0];
    return opt && opt.value ? opt.textContent.trim() : "";
  }

  function readPayload(publish) {
    const studentId = parseInt(filterValue("student_id"), 10);
    const termRaw = filterValue("term_id");
    const subjectRaw = filterValue("subject_id");
    const body = (bodyEl.value || "").trim();
    if (!studentId || !body) {
      return null;
    }
    const payload = {
      student_id: studentId,
      body: body,
      category: categoryEl.value,
      publish: !!publish,
    };
    if (termRaw) {
      payload.term_id = parseInt(termRaw, 10);
    }
    if (subjectRaw) {
      payload.subject_id = parseInt(subjectRaw, 10);
    }
    return payload;
  }

  function showStatus(kind, message) {
    statusEl.hidden = false;
    statusEl.className = "alert alert--" + kind;
    statusEl.textContent = message;
  }

  function clearStatus() {
    statusEl.hidden = true;
    statusEl.textContent = "";
  }

  function renderTimelineItem(item, target) {
    const entry = document.createElement("div");
    entry.className = "timeline__item";
    const meta = document.createElement("div");
    meta.className = "timeline__meta";
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = item.category;
    meta.appendChild(chip);
    ["term", "teacher", "subject"].forEach(function (key) {
      if (item[key] && item[key] !== "General") {
        const span = document.createElement("span");
        span.textContent = item[key];
        meta.appendChild(span);
      }
    });
    entry.appendChild(meta);
    const body = document.createElement("div");
    body.className = "timeline__body";
    body.textContent = item.body;
    entry.appendChild(body);
    target.appendChild(entry);
  }

  function updatePreview() {
    const body = (bodyEl.value || "").trim();
    const studentId = filterValue("student_id");
    previewEl.innerHTML = "";
    if (!studentId || !body) {
      previewNote.hidden = false;
      previewNote.textContent = studentId
        ? "Enter remark text to preview the family timeline."
        : "Select a student and enter text to preview.";
      return;
    }
    previewNote.hidden = false;
    previewNote.textContent =
      "Families see this layout after you publish. Drafts never appear on parent or student portals.";
    const categoryLabel = categoryLabels[categoryEl.value] || categoryEl.value;
    renderTimelineItem(
      {
        category: categoryLabel,
        term: selectedLabel("term_id") || "--",
        teacher: teacherName,
        subject: selectedLabel("subject_id") || "General",
        body: body,
      },
      previewEl
    );
    previewNote.hidden = true;
  }

  function loadPublishedFamilyView() {
    const studentId = filterValue("student_id");
    publishedList.innerHTML = "";
    if (!studentId) {
      publishedWrap.hidden = true;
      return;
    }
    const params = new URLSearchParams({ student_id: studentId });
    const termId = filterValue("term_id");
    const subjectId = filterValue("subject_id");
    if (termId) params.set("term_id", termId);
    if (subjectId) params.set("subject_id", subjectId);
    fetch("/api/charts/student.remarks?" + params.toString(), {
      credentials: "same-origin",
    })
      .then(function (res) {
        return res.ok ? res.json() : { items: [] };
      })
      .then(function (payload) {
        const items = (payload.items || []).filter(function (item) {
          return item.visibility !== "draft";
        });
        if (!items.length) {
          publishedWrap.hidden = true;
          return;
        }
        publishedWrap.hidden = false;
        items.slice(0, 5).forEach(function (item) {
          renderTimelineItem(item, publishedList);
        });
      });
  }

  function submitRemark(publish) {
    clearStatus();
    const payload = readPayload(publish);
    if (!payload) {
      showStatus("error", "Choose a student and write a remark before saving.");
      return;
    }
    fetch("/api/remarks", {
      method: "POST",
      credentials: "same-origin",
      headers: csrfHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(payload),
    })
      .then(function (res) {
        return res.json().then(function (data) {
          return { ok: res.ok, status: res.status, data: data };
        });
      })
      .then(function (result) {
        if (!result.ok) {
          const detail = (result.data && result.data.detail) || "Could not save remark.";
          showStatus("error", typeof detail === "string" ? detail : "Could not save remark.");
          return;
        }
        savedDraftId = result.data.id;
        if (result.data.visibility === "published") {
          followUpEl.hidden = true;
          showStatus("success", "Published — guardians can read this remark now.");
        } else {
          followUpEl.hidden = false;
          showStatus("success", "Draft saved — only staff can see it until you publish.");
        }
        loadPublishedFamilyView();
      });
  }

  function publishSavedDraft() {
    if (!savedDraftId) return;
    clearStatus();
    fetch("/api/remarks/" + savedDraftId + "/publish", {
      method: "POST",
      credentials: "same-origin",
      headers: csrfHeaders({ "Content-Type": "application/json" }),
    })
      .then(function (res) {
        return res.json().then(function (data) {
          return { ok: res.ok, data: data };
        });
      })
      .then(function (result) {
        if (!result.ok) {
          showStatus("error", "Could not publish draft.");
          return;
        }
        followUpEl.hidden = true;
        showStatus("success", "Published — guardians can read this remark now.");
        loadPublishedFamilyView();
      });
  }

  document.getElementById("remark-save-draft").addEventListener("click", function () {
    submitRemark(false);
  });
  document.getElementById("remark-publish").addEventListener("click", function () {
    submitRemark(true);
  });
  publishSavedBtn.addEventListener("click", publishSavedDraft);

  bodyEl.addEventListener("input", updatePreview);
  categoryEl.addEventListener("change", updatePreview);
  document.addEventListener("filters:changed", function () {
    savedDraftId = null;
    followUpEl.hidden = true;
    updatePreview();
    loadPublishedFamilyView();
  });

  updatePreview();
  loadPublishedFamilyView();
})();
