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

  function columnNumber(column, name) {
    return Number(column.dataset[name] || 0);
  }

  function cardCount(column) {
    return column.querySelectorAll(".kanban-dropzone .lead-card").length;
  }

  function syncColumnEmptyState(column) {
    var dropzone = column.querySelector(".kanban-dropzone");
    if (!dropzone) return;
    var empty = dropzone.querySelector(".column-empty");
    if (!cardCount(column) && columnNumber(column, "total") === 0) {
      if (!empty) {
        empty = document.createElement("p");
        empty.className = "column-empty";
        empty.textContent = "No leads here";
        dropzone.appendChild(empty);
      }
    } else if (empty) {
      empty.remove();
    }
  }

  function setColumnState(column, total, shown) {
    total = Math.max(0, total);
    shown = Math.max(0, shown);
    column.dataset.total = String(total);
    column.dataset.shown = String(shown);
    column.dataset.nextOffset = String(shown);

    var count = column.querySelector("[data-column-total]");
    if (count) {
      count.textContent = count.dataset.countFormat === "parenthesized" ? "(" + total + ")" : String(total);
    }
    var summary = column.querySelector("[data-column-summary]");
    if (summary) summary.textContent = "Showing " + shown + " of " + total;
    var loadMore = column.querySelector("[data-load-more]");
    if (loadMore) loadMore.hidden = shown >= total;
    syncColumnEmptyState(column);
  }

  function updateColumnAfterMove(column, totalChange) {
    setColumnState(column, columnNumber(column, "total") + totalChange, cardCount(column));
  }

  function relativeActivityTime(value) {
    var date = new Date(value);
    if (Number.isNaN(date.getTime())) return "Recently";
    var seconds = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000));
    if (seconds < 60) return "Just now";
    var units = [
      [31536000, "year"],
      [2592000, "month"],
      [86400, "day"],
      [3600, "hour"],
      [60, "minute"]
    ];
    for (var index = 0; index < units.length; index += 1) {
      var unit = units[index];
      if (seconds >= unit[0]) {
        var amount = Math.floor(seconds / unit[0]);
        return amount + " " + unit[1] + (amount === 1 ? "" : "s") + " ago";
      }
    }
    return "Recently";
  }

  function quotedPrice(value) {
    var number = Number(value);
    if (!value || Number.isNaN(number) || number <= 0) return "No quote yet";
    return "Rs " + new Intl.NumberFormat("en-PK", { maximumFractionDigits: 0 }).format(number);
  }

  function leadInitials(name) {
    return String(name || "L").trim().split(/\s+/).slice(0, 2).map(function (part) {
      return part.charAt(0);
    }).join("").toUpperCase() || "L";
  }

  function leadCardFromPayload(lead) {
    var card = document.createElement("article");
    card.className = "lead-card";
    card.draggable = true;
    card.dataset.leadId = lead.id;
    card.dataset.stage = lead.stage;
    card.dataset.transitionUrl = lead.transition_url;
    card.dataset.detailUrl = lead.detail_url;

    var link = document.createElement("a");
    link.className = "lead-card-link";
    link.href = lead.detail_url;

    var primary = document.createElement("div");
    primary.className = "lead-card-primary";
    var avatar = document.createElement("span");
    avatar.className = "lead-card-avatar";
    avatar.setAttribute("aria-hidden", "true");
    avatar.textContent = leadInitials(lead.customer_name);
    var copy = document.createElement("span");
    copy.className = "lead-card-copy";
    var name = document.createElement("strong");
    name.textContent = lead.customer_name;
    var product = document.createElement("small");
    product.textContent = lead.product_name || "Service to be confirmed";
    copy.appendChild(name);
    copy.appendChild(product);
    primary.appendChild(avatar);
    primary.appendChild(copy);

    var meta = document.createElement("div");
    meta.className = "lead-card-meta";
    var price = document.createElement("span");
    price.textContent = quotedPrice(lead.quoted_price);
    var activity = document.createElement("time");
    activity.dateTime = lead.last_activity_at;
    activity.textContent = relativeActivityTime(lead.last_activity_at);
    meta.appendChild(price);
    meta.appendChild(activity);

    link.appendChild(primary);
    link.appendChild(meta);
    card.appendChild(link);
    return card;
  }

  function columnAlreadyShowsLead(column, leadId) {
    return Array.prototype.some.call(column.querySelectorAll(".lead-card"), function (card) {
      return card.dataset.leadId === String(leadId);
    });
  }

  function setLoadMoreBusy(button, busy) {
    if (busy) {
      button.dataset.label = button.textContent;
      button.textContent = "Loading…";
      button.disabled = true;
    } else {
      button.textContent = button.dataset.label || "Load more";
      button.disabled = false;
    }
  }

  function loadMoreLeads(board, column, button) {
    var apiUrl = board.dataset.kanbanApiUrl;
    if (!apiUrl || button.disabled) return;
    var params = new URLSearchParams({
      stage: column.dataset.stage,
      limit: "10",
      offset: String(columnNumber(column, "shown"))
    });
    if (board.dataset.search) params.set("search", board.dataset.search);
    if (board.dataset.assignedUser) params.set("assigned_user", board.dataset.assignedUser);
    var separator = apiUrl.indexOf("?") === -1 ? "?" : "&";
    setLoadMoreBusy(button, true);

    fetch(apiUrl + separator + params.toString(), {
      headers: { "Accept": "application/json" },
      credentials: "same-origin"
    }).then(function (response) {
      if (!response.ok) throw new Error("lead page failed");
      return response.json();
    }).then(function (payload) {
      if (!payload || !Array.isArray(payload.results)) throw new Error("invalid lead page");
      var dropzone = column.querySelector(".kanban-dropzone");
      payload.results.forEach(function (lead) {
        if (!columnAlreadyShowsLead(column, lead.id)) dropzone.appendChild(leadCardFromPayload(lead));
      });
      setColumnState(column, Number(payload.count || 0), cardCount(column));
      if (!payload.results.length) button.hidden = true;
    }).catch(function () {
      notice("Cuein could not load more leads. Please try again.");
    }).finally(function () {
      setLoadMoreBusy(button, false);
    });
  }

  function setUpKanban() {
    var board = document.querySelector("[data-kanban]");
    if (!board) return;

    board.addEventListener("click", function (event) {
      var loadMore = event.target.closest("[data-load-more]");
      if (!loadMore || !board.contains(loadMore)) return;
      loadMoreLeads(board, loadMore.closest(".kanban-column"), loadMore);
    });

    if (!("draggable" in document.createElement("span"))) return;
    var draggedCard = null;

    board.addEventListener("dragstart", function (event) {
      var card = event.target.closest(".lead-card");
      if (!card || !board.contains(card)) return;
      draggedCard = card;
      card.classList.add("is-dragging");
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", card.dataset.detailUrl || "");
    });

    board.addEventListener("dragend", function () {
      if (draggedCard) draggedCard.classList.remove("is-dragging");
      board.querySelectorAll(".kanban-column").forEach(function (column) {
        column.classList.remove("is-drop-target");
      });
      draggedCard = null;
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

        var card = draggedCard;
        var sourceColumn = card.closest(".kanban-column");
        var destination = column.querySelector(".kanban-dropzone");
        var originalStage = card.dataset.stage;
        card.classList.add("is-dragging");

        fetch(card.dataset.transitionUrl, {
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
          destination.prepend(card);
          card.dataset.stage = column.dataset.stage;
          updateColumnAfterMove(sourceColumn, -1);
          updateColumnAfterMove(column, 1);
          notice("Lead moved to " + (column.querySelector("header h2") || {}).textContent + ".");
        }).catch(function () {
          card.dataset.stage = originalStage;
          notice("Cuein could not move this lead. Open it and try again.");
        }).finally(function () {
          card.classList.remove("is-dragging");
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

  function setUpAssigneeDropdowns() {
    document.querySelectorAll("[data-assignee-dropdown]").forEach(function (dropdown) {
      dropdown.addEventListener("change", function (event) {
        var input = event.target.closest(".assignee-dropdown-input");
        if (!input || !dropdown.contains(input)) return;

        var option = input.closest("[data-assignee-option]");
        var selected = dropdown.querySelector("[data-assignee-selected]");
        if (!option || !selected) return;

        var selectedImage = selected.querySelector("[data-assignee-selected-image]");
        var selectedName = selected.querySelector("[data-assignee-selected-name]");
        var selectedDetail = selected.querySelector("[data-assignee-selected-detail]");
        if (selectedImage) selectedImage.src = option.dataset.assigneeAvatar || dropdown.dataset.fallbackAvatar;
        if (selectedName) selectedName.textContent = option.dataset.assigneeName || "Assigned user";
        if (selectedDetail) selectedDetail.textContent = option.dataset.assigneeDetail || "";
        dropdown.open = false;
      });
    });
  }

  function setUpLeadTrendTooltips() {
    document.querySelectorAll("[data-lead-trend-chart]").forEach(function (chart) {
      var tooltip = chart.querySelector("[data-lead-trend-tooltip]");
      var primary = chart.querySelector("[data-lead-trend-tooltip-primary]");
      var deltaLine = chart.querySelector("[data-lead-trend-tooltip-delta]");
      var points = Array.prototype.slice.call(chart.querySelectorAll("[data-lead-trend-point]"));
      var activePoint = null;
      if (!tooltip || !primary || !deltaLine || !points.length) return;

      function leadLabel(count) {
        return count + " new lead" + (count === 1 ? "" : "s");
      }

      function hideTooltip() {
        if (activePoint) activePoint.classList.remove("is-active");
        activePoint = null;
        tooltip.hidden = true;
      }

      function positionTooltip(point) {
        var chartRect = chart.getBoundingClientRect();
        var pointRect = point.getBoundingClientRect();
        var padding = 8;
        var left = pointRect.left - chartRect.left - (tooltip.offsetWidth / 2);
        var maxLeft = Math.max(padding, chart.clientWidth - tooltip.offsetWidth - padding);
        var above = pointRect.top - chartRect.top - tooltip.offsetHeight - 14;
        var top = above < padding
          ? Math.min(chart.clientHeight - tooltip.offsetHeight - padding, pointRect.bottom - chartRect.top + 14)
          : above;

        tooltip.style.left = Math.max(padding, Math.min(left, maxLeft)) + "px";
        tooltip.style.top = Math.max(padding, top) + "px";
      }

      function showTooltip(point) {
        var pointIndex = points.indexOf(point);
        var count = Number(point.dataset.count);
        var date = point.dataset.date || "Selected day";
        if (pointIndex < 0 || Number.isNaN(count)) return;

        if (activePoint && activePoint !== point) activePoint.classList.remove("is-active");
        activePoint = point;
        activePoint.classList.add("is-active");
        primary.textContent = date + " — " + leadLabel(count);

        if (pointIndex === 0) {
          deltaLine.hidden = true;
        } else {
          var previousPoint = points[pointIndex - 1];
          var previousCount = Number(previousPoint.dataset.count);
          var delta = count - previousCount;
          var sign = delta > 0 ? "+" : "";
          deltaLine.hidden = false;
          deltaLine.textContent = sign + delta + " vs. " + (previousPoint.dataset.date || "previous day");
        }

        tooltip.hidden = false;
        positionTooltip(point);
      }

      points.forEach(function (point) {
        point.addEventListener("mouseenter", function () { showTooltip(point); });
        point.addEventListener("focus", function () { showTooltip(point); });
        point.addEventListener("click", function () { showTooltip(point); });
        point.addEventListener("blur", function () {
          window.setTimeout(function () {
            if (!chart.contains(document.activeElement)) hideTooltip();
          }, 0);
        });
      });

      chart.addEventListener("mouseleave", function () {
        if (!chart.contains(document.activeElement)) hideTooltip();
      });
    });
  }

  // Delegate this at script-load time instead of wiring each button during
  // page setup. A toast can then always be dismissed even if another optional
  // enhancement on the page fails to initialize.
  document.addEventListener("click", function (event) {
    var target = event.target;
    var button = target && typeof target.closest === "function"
      ? target.closest("[data-flash-dismiss]")
      : null;
    if (!button) return;

    var message = button.closest("[data-flash-message]");
    if (message) {
      event.preventDefault();
      message.remove();
    }
  });

  function setUpLiveClocks() {
    document.querySelectorAll("[data-live-clock]").forEach(function (clock) {
      var options = {
        hour: "numeric",
        minute: "2-digit",
        second: "2-digit",
        hour12: true
      };
      var timeZone = clock.dataset.timeZone;
      if (timeZone) options.timeZone = timeZone;

      var formatter;
      try {
        formatter = new Intl.DateTimeFormat("en-PK", options);
      } catch (error) {
        delete options.timeZone;
        formatter = new Intl.DateTimeFormat("en-PK", options);
      }

      function updateClock() {
        var now = new Date();
        clock.dateTime = now.toISOString();
        clock.textContent = formatter.format(now);
      }

      updateClock();
      window.setTimeout(function () {
        updateClock();
        window.setInterval(updateClock, 1000);
      }, 1000 - (Date.now() % 1000));
    });
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

  function setUpPasswordToggles() {
    document.querySelectorAll("[data-password-toggle]").forEach(function (toggle) {
      var input = document.getElementById(toggle.getAttribute("aria-controls"));
      if (!input) return;

      toggle.addEventListener("click", function () {
        var shouldShowPassword = input.type === "password";
        input.type = shouldShowPassword ? "text" : "password";
        toggle.setAttribute("aria-pressed", String(shouldShowPassword));
        toggle.setAttribute("aria-label", shouldShowPassword ? "Hide password" : "Show password");
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    setUpPasswordToggles();
    setUpKanban();
    setUpStageForm();
    setUpAssigneeDropdowns();
    setUpLeadTrendTooltips();
    setUpLiveClocks();
    setUpSidebarToggle();
  });
}());
