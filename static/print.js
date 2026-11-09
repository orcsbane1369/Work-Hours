(() => {
  "use strict";
  const b = document.getElementById("printBtn");
  if (b) b.addEventListener("click", () => window.print());
  // Opened from the app's "Print / Save as PDF" button: bring up the print dialog straight away.
  if (document.querySelector('meta[name="autoprint"]')) window.addEventListener("load", () => setTimeout(() => window.print(), 300));
})();
