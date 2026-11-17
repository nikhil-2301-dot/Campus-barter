/* Browse page: while a search is being sent, swap the results for the loading skeleton.
   Without JavaScript the form is a normal GET and everything still works. */
(function () {
  "use strict";
  var form = document.getElementById("browse-form");
  var results = document.getElementById("browse-results");
  var loading = document.getElementById("browse-loading");
  if (!form || !results || !loading) { return; }

  function showResults() {
    results.hidden = false;
    loading.hidden = true;
  }

  form.addEventListener("submit", function () {
    results.hidden = true;
    loading.hidden = false;
  });
  // Coming back with the Back button must not leave the skeleton on screen.
  window.addEventListener("pageshow", showResults);
})();
