/* Lighthouse Static Site — site.js */
/* Optional enhancements: copy buttons, active nav highlight */

(function () {
  "use strict";

  /* ---- Copy-to-clipboard for code blocks ---- */
  function initCopyButtons() {
    document.querySelectorAll("pre").forEach(function (pre) {
      var wrapper = document.createElement("div");
      wrapper.className = "code-block";
      pre.parentNode.insertBefore(wrapper, pre);
      wrapper.appendChild(pre);

      var btn = document.createElement("button");
      btn.className = "copy-btn";
      btn.textContent = "copy";
      wrapper.appendChild(btn);

      btn.addEventListener("click", function () {
        var text = pre.textContent || "";
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(function () {
            btn.textContent = "copied!";
            btn.classList.add("copied");
            setTimeout(function () {
              btn.textContent = "copy";
              btn.classList.remove("copied");
            }, 1800);
          });
        }
      });
    });
  }

  /* ---- Active navigation highlight ---- */
  function initActiveNav() {
    var path = window.location.pathname;
    var file = path.split("/").pop() || "index.html";
    document.querySelectorAll(".sidebar a").forEach(function (a) {
      var href = a.getAttribute("href");
      if (href && (href === file || href.split("/").pop() === file)) {
        a.classList.add("active");
      }
    });
  }

  /* ---- Smooth scroll for anchor links ---- */
  function initAnchorScroll() {
    document.querySelectorAll('a[href^="#"]').forEach(function (a) {
      a.addEventListener("click", function (e) {
        var id = a.getAttribute("href").slice(1);
        var target = document.getElementById(id);
        if (target) {
          e.preventDefault();
          target.scrollIntoView({ behavior: "smooth", block: "start" });
        }
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    initCopyButtons();
    initActiveNav();
    initAnchorScroll();
  });
})();
