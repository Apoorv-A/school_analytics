/** Fills the sign-in form from a demo account button. Only present when the
 *  database still holds seeded demo users. */
(function () {
  "use strict";

  document.querySelectorAll(".demo-account").forEach(function (button) {
    button.addEventListener("click", function () {
      const email = document.getElementById("email");
      const password = document.getElementById("password");
      if (email) email.value = button.getAttribute("data-email") || "";
      if (password) password.value = button.getAttribute("data-password") || "";
      if (password) password.focus();
    });
  });
})();
