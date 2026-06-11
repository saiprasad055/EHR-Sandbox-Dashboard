(function () {
  var SRC = window.__FIELD_SRC__ || {};

  function closeAllTipCards() {
    document.querySelectorAll(".field-prov .tip-card").forEach(function (c) {
      c.hidden = true;
    });
    document.querySelectorAll(".field-prov .info-tip").forEach(function (b) {
      b.setAttribute("aria-expanded", "false");
    });
  }

  document.querySelectorAll(".field-prov .info-tip").forEach(function (btn) {
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      var wrap = btn.closest(".field-prov");
      if (!wrap) return;
      var key = wrap.getAttribute("data-field");
      var card = wrap.querySelector(".tip-card");
      if (!card) return;
      var meta = SRC[key] || { resource: "not found", path: "not found" };
      var resEl = card.querySelector(".tip-resource");
      var pathEl = card.querySelector(".tip-path");
      if (resEl) resEl.textContent = meta.resource || "not found";
      if (pathEl) pathEl.textContent = meta.path || "not found";
      var opening = card.hidden;
      closeAllTipCards();
      card.hidden = !opening;
      btn.setAttribute("aria-expanded", opening ? "true" : "false");
    });
  });

  document.addEventListener("click", function () {
    closeAllTipCards();
  });

  document.querySelectorAll(".field-prov .tip-card").forEach(function (card) {
    card.addEventListener("click", function (e) {
      e.stopPropagation();
    });
  });

  var slotSel = document.getElementById("user-slot-select");
  if (slotSel) {
    slotSel.addEventListener("change", function () {
      var v = slotSel.value;
      if (v) window.location.href = v;
    });
  }
})();
