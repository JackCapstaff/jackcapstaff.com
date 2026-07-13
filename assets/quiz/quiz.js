/* SQE Practice App – quiz.js */
// Minimal JS — most logic lives in inline scripts on each page.
// This file handles global utility functions only.

document.addEventListener('DOMContentLoaded', function () {
  // Auto-dismiss alerts after 6 seconds
  document.querySelectorAll('.alert').forEach(function (el) {
    setTimeout(function () {
      el.style.transition = 'opacity 0.5s';
      el.style.opacity = '0';
      setTimeout(function () { el.remove(); }, 500);
    }, 6000);
  });
});
