/* Opens and closes native <dialog> elements.
   data-dialog-open="ID" on a button opens the dialog; data-dialog-close inside a dialog closes it.
   Without JavaScript the dialog stays closed (hidden by the browser), so pages must not rely on it
   as the only way to reach an action. */
(function () {
  "use strict";
  /* Progressive enhancement: confirm_open() draws a plain link (data-no-js) and a hidden dialog button
     (data-needs-js). When the browser can show a modal dialog, swap one for the other. */
  if (typeof document.createElement("dialog").showModal === "function") {
    document.querySelectorAll("[data-needs-js]").forEach(function (el) { el.hidden = false; });
    document.querySelectorAll("[data-no-js]").forEach(function (el) { el.hidden = true; });
  }
  document.addEventListener("click", function (event) {
    var opener = event.target.closest("[data-dialog-open]");
    if (opener) {
      var dialog = document.getElementById(opener.getAttribute("data-dialog-open"));
      if (dialog && typeof dialog.showModal === "function" && !dialog.open) {
        dialog.showModal();
      }
      return;
    }
    var closer = event.target.closest("[data-dialog-close]");
    if (closer) {
      var parent = closer.closest("dialog");
      if (parent) { parent.close(); }
      return;
    }
    /* a click on the dark backdrop closes it: the click lands on the <dialog> itself but outside its box */
    if (event.target.tagName === "DIALOG" && event.target.open) {
      var box = event.target.getBoundingClientRect();
      var outside = event.clientX < box.left || event.clientX > box.right ||
                    event.clientY < box.top || event.clientY > box.bottom;
      if (outside) { event.target.close(); }
    }
  });
})();
