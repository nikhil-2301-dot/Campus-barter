/* Request pages: after a form is sent, disable its buttons so a second click cannot send it twice.
   The server is safe either way (one open request per item, one state change at a time); this only
   saves a confusing second message. Without JavaScript nothing changes. */
(function () {
  "use strict";
  function enable() {
    document.querySelectorAll("form[method='post'] button[disabled][data-submitted]").forEach(function (b) {
      b.disabled = false;
      b.removeAttribute("data-submitted");
    });
  }
  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form || form.tagName !== "FORM") { return; }
    // Disable after the browser has collected the form data, so nothing is lost.
    window.setTimeout(function () {
      form.querySelectorAll("button[type='submit'], button:not([type])").forEach(function (b) {
        b.disabled = true;
        b.setAttribute("data-submitted", "");
      });
    }, 0);
  });
  // Coming back with the Back button must not leave the buttons disabled.
  window.addEventListener("pageshow", enable);
})();
