/*
 * Small progressive enhancements for the server-rendered UI. Every workflow
 * still has a normal form or link when JavaScript is unavailable.
 */
(function () {
  "use strict";

  function csrfToken() {
    var name = "csrftoken=";
    return document.cookie.split(";").map(function (part) {
      return part.trim();
    }).filter(function (part) {
      return part.indexOf(name) === 0;
    }).map(function (part) {
      return decodeURIComponent(part.slice(name.length));
    })[0] || "";
  }

  function notice(message) {
    var existing = document.querySelector(".client-notice");
    if (existing) existing.remove();
    var element = document.createElement("div");
    element.className = "client-notice";
    element.textContent = message;
    document.body.appendChild(element);
    window.setTimeout(function () { element.remove(); }, 4200);
  }

  function updateColumnCount(column, change) {
    var count = column.querySelector("header strong");
    if (!count) return;
    count.textContent = Math.max(0, Number(count.textContent || 0) + change);
  }

  function setUpKanban() {
    var board = document.querySelector("[data-kanban]");
    if (!board || !("draggable" in document.createElement("span"))) return;

    var draggedCard = null;
    board.querySelectorAll(".lead-card").forEach(function (card) {
      card.addEventListener("dragstart", function (event) {
        draggedCard = card;
        card.classList.add("is-dragging");
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", card.dataset.detailUrl || "");
      });
      card.addEventListener("dragend", function () {
        card.classList.remove("is-dragging");
        board.querySelectorAll(".kanban-column").forEach(function (column) {
          column.classList.remove("is-drop-target");
        });
        draggedCard = null;
      });
    });

    board.querySelectorAll(".kanban-column").forEach(function (column) {
      column.addEventListener("dragover", function (event) {
        if (!draggedCard || column.dataset.stage === draggedCard.dataset.stage) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = "move";
        column.classList.add("is-drop-target");
      });
      column.addEventListener("dragleave", function () {
        column.classList.remove("is-drop-target");
      });
      column.addEventListener("drop", function (event) {
        event.preventDefault();
        column.classList.remove("is-drop-target");
        if (!draggedCard || column.dataset.stage === draggedCard.dataset.stage) return;

        if (column.dataset.requiresReason === "true") {
          notice("A lost lead needs a reason. Add it from the lead page.");
          window.location.assign(draggedCard.dataset.detailUrl + "#move-lead");
          return;
        }

        var sourceColumn = draggedCard.closest(".kanban-column");
        var destination = column.querySelector(".kanban-dropzone");
        var originalStage = draggedCard.dataset.stage;
        draggedCard.classList.add("is-dragging");

        fetch(draggedCard.dataset.transitionUrl, {
          method: "POST",
          headers: {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "X-CSRFToken": csrfToken(),
            "X-Requested-With": "XMLHttpRequest"
          },
          body: new URLSearchParams({ stage: column.dataset.stage }).toString(),
          credentials: "same-origin"
        }).then(function (response) {
          if (!response.ok) throw new Error("stage update failed");
          return response.json();
        }).then(function () {
          destination.prepend(draggedCard);
          draggedCard.dataset.stage = column.dataset.stage;
          updateColumnCount(sourceColumn, -1);
          updateColumnCount(column, 1);
          notice("Lead moved to " + (column.querySelector("header h2") || {}).textContent + ".");
        }).catch(function () {
          draggedCard.dataset.stage = originalStage;
          notice("Cuein could not move this lead. Open it and try again.");
        }).finally(function () {
          draggedCard.classList.remove("is-dragging");
        });
      });
    });
  }

  function setUpStageForm() {
    var form = document.querySelector(".stage-change-form");
    if (!form) return;
    var select = form.querySelector("select[name='stage']");
    var lostReason = form.querySelector(".lost-reason-field");
    if (!select || !lostReason) return;
    function syncLostReason() {
      var isLost = select.value === "lost";
      lostReason.hidden = !isLost;
      var input = lostReason.querySelector("textarea, input");
      if (input) input.required = isLost;
    }
    select.addEventListener("change", syncLostReason);
    syncLostReason();
  }

  function setUpSidebarToggle() {
    var toggle = document.querySelector("[data-sidebar-toggle]");
    var sidebar = document.getElementById("workspace-sidebar");
    if (!toggle || !sidebar) return;

    var storageKey = "cuein.sidebarCollapsed";
    var desktopQuery = window.matchMedia("(min-width: 681px)");

    function syncSidebar() {
      var isCollapsed = document.documentElement.classList.contains("sidebar-is-collapsed");
      var isHidden = desktopQuery.matches && isCollapsed;
      toggle.setAttribute("aria-expanded", String(!isCollapsed));
      toggle.setAttribute("aria-label", isCollapsed ? "Open navigation" : "Close navigation");
      sidebar.setAttribute("aria-hidden", String(isHidden));
      sidebar.inert = isHidden;
    }

    toggle.addEventListener("click", function () {
      var isCollapsed = document.documentElement.classList.toggle("sidebar-is-collapsed");
      try {
        window.localStorage.setItem(storageKey, String(isCollapsed));
      } catch (error) {}
      syncSidebar();
    });

    if (desktopQuery.addEventListener) {
      desktopQuery.addEventListener("change", syncSidebar);
    } else {
      desktopQuery.addListener(syncSidebar);
    }
    syncSidebar();
  }

  document.addEventListener("DOMContentLoaded", function () {
    setUpKanban();
    setUpStageForm();
    setUpSidebarToggle();
  });
}());
